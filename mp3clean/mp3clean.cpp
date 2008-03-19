#define _FILE_OFFSET_BITS 64
#define _XOPEN_SOURCE 500

extern "C" {
	#include <stdlib.h>
	#include <string.h>
	#include <errno.h>
	#include <assert.h>
	#include <stdio.h>
	#include <sys/types.h>
	#include <unistd.h>
	#include <ftw.h>
	#include <openssl/sha.h>
	#include "../eruutil/strrcasestr.h"
	#include "../eruutil/memnotchr.h"
	#include "../eruutil/erudebug.h"
}

#include <vector>
#include <string>

using namespace std;

typedef enum {no = 0, yes} has_t;

#define MIN(a, b) ((a < b) ? a : b)
#define MAX(a, b) ((a > b) ? a : b)
#define DATAHASH_DIGEST_LENGTH SHA_DIGEST_LENGTH

struct Datahash {
	unsigned char md[DATAHASH_DIGEST_LENGTH];
	bool hashed;
	off_t start, end;
	string name;
	Datahash(): hashed(false), start(-1), end(-1) {}
};

vector<Datahash> g_hashes;

static_assert(sizeof(size_t) >= sizeof(long));
static_assert(sizeof(off_t) >= sizeof(long));
static_assert(sizeof(off_t) == sizeof(long long));

int sha1_file_ex(
	FILE *fstream,
	unsigned char msgdgst[],
	off_t start,
	off_t end)
{
	assert(end >= start && end >= 0 && start >= 0);
	int ret = 1;
	void *filedata = NULL;
	long bufsize = sysconf(_SC_PAGESIZE);

	do {
		if (fseek(fstream, start, SEEK_SET)) {
			warn(errno, "fseeko()");
			break;
		}

		filedata = malloc(bufsize);
		if (!filedata) {
			warn(errno, "malloc()");
			break;
		}

		SHA_CTX shactx;
		if (SHA1_Init(&shactx) != 1) break;

		size_t hashcnt = 0;
		do {
			size_t reqcnt = MIN(end - start - hashcnt, bufsize);
			size_t readcnt = fread(filedata, 1, reqcnt, fstream);
			if (readcnt < reqcnt && (feof(fstream) || ferror(fstream))) {
				warn(errno, "fread()");
				break;
			}
			assert(readcnt == reqcnt);
			if (SHA1_Update(&shactx, filedata, readcnt) != 1) break;
			hashcnt += readcnt;
		} while (hashcnt < end - start);
		if (hashcnt < end - start) break;
		assert(hashcnt == end - start);

		if (SHA1_Final(msgdgst, &shactx) != 1) break;

		ret = 0;
	} while (0);

	if (filedata) free(filedata);
	return ret;
}

has_t get_id3v2(FILE *fs, off_t *size)
{
	assert(fs);
	has_t ret = no;
	if (size != NULL) *size = 0;
	unsigned char v2hdr[10];
	if (fseek(fs, 0, SEEK_SET)) {
		warn(errno, "fseek()");
		goto done;
	}
	{
		size_t read = fread(v2hdr, 1, sizeof(v2hdr), fs);
		if (read < sizeof(v2hdr)) {
			if (feof(fs) || ferror(fs)) warn(errno, "fread()");
			else assert(0);
			goto done;
		}
	}
	// file ident
	if (strncmp((char *)&v2hdr[0], "ID3", 3)) goto done;
	// version
	if (v2hdr[3] == 0xff || v2hdr[4] == 0xff) goto done;
	// flags
	if (v2hdr[5] != 0) {
		warn(0, "TODO: contains unimplemented ID3v2 flags");
		assert(0);
		goto done;
	}
	// 7 bit, bigendian size
	{
		int i;
		for (i = 6; i < 10; i++) {
			if (v2hdr[i] >> 7 || v2hdr[i] & 0x80) {
				assert(v2hdr[i] >> 7 && v2hdr[i] & 0x80);
				break;
			}
		}
		assert(i == 10);
		if (i != 10) goto done;
	}
	// get 28 bit size
	if (size != NULL) {
		*size = 10;
		long flagval = 0;
		for (int i = 0; i < 4; i++) {
			long incval = v2hdr[9 - i];
			incval = incval << (i * 7);
			flagval |= incval;
		}
		*size += flagval;
	}
	ret = yes;
done:
	return ret;
}

has_t get_id3v1(FILE *fs)
{
	assert(fs);
	const static size_t TAG_SIZE = 128;
	has_t ret = no;
	char *v1tag = NULL;
	if (fseek(fs, -TAG_SIZE, SEEK_END)) {
		warn(errno, "fseek()");
		goto done;
	}
	v1tag = (char *)malloc(TAG_SIZE);
	if (v1tag == NULL) {
		warn(errno, "malloc()");
		goto done;
	}
	{
		size_t read = fread(v1tag, 1, TAG_SIZE, fs);
		if (read < TAG_SIZE) {
			if (feof(fs) || ferror(fs)) warn(errno, "fread()");
			else assert(0);
			goto done;
		}
	}
	if (strncmp(&v1tag[0], "TAG", 3)) goto done;
	ret = yes;
done:
	if (v1tag != NULL) free(v1tag);
	return ret;
}

off_t get_next_frame_sync(FILE *fs, off_t start)
{
	off_t ret = -1;
	if (fseeko(fs, start, SEEK_SET)) {
		warn(errno, "fseek()");
		goto done;
	}
	for (;;) {
		int c = fgetc(fs);
		if (c == 0xff) {
			c = fgetc(fs);
			if ((c & 0xe0) == 0xe0) break;
		}
		if (c == EOF) break;
	}
	if (feof(fs) || ferror(fs)) {
		warn(errno, "fgetc()");
		goto done;
	}
	ret = ftello(fs) - 2;
done:
	return ret;
}

int mp3_data_bounds(FILE *fs, off_t *start, off_t *end)
{
	int ret = 1;

	// get start of mp3 data
	get_id3v2(fs, start);
	// check data starts where id3v2 tag ends
#ifndef NO_FRAME_SYNC
	off_t frameoff = get_next_frame_sync(fs, *start);
	assert(frameoff != -1);
	if (frameoff != -1) {
		if (frameoff != *start) {
			debugln("id3v2 tag end (%llu) does not match frame sync (%llu)",
				*start, frameoff);
		}
		// adjust data start if frame sync after end of id3v2 tag
		if (frameoff > *start) *start = frameoff;
	}
#endif
	// get end of mp3 data
	if (fseek(fs, 0, SEEK_END)) {
		warn(errno, "fseek()");
		goto done;
	}
	*end = ftell(fs);
	if (get_id3v1(fs)) *end -= 128;

	ret = 0;
done:
	return ret;
}

int sha1_mp3_data(FILE *fs, unsigned char md[])
{
	int ret = 1;

	off_t start, end;
	if (mp3_data_bounds(fs, &start, &end)) {
		debugln("mp3_data_bounds() failed");
		goto done;
	}

	// hash mp3 data
	if (sha1_file_ex(fs, md, start, end)) {
		warn(0, "sha1_file_ex() failed");
		goto done;
	}

	for (int i = 0; i < 20; i++) debug("%02x", md[i]);
	debug("\n");

	ret = 0;

done:
	return ret;
}

int has_mp3_ext(const char *name)
{
	static const char MP3_EXT[] = ".mp3";
	return (strrcasestr(name, ".mp3") == name + strlen(name) - strlen(MP3_EXT));
}

int parse_mp3_size_callback(
	const char *fpath,
	const struct stat *sb,
	int typeflag,
	struct FTW *ftwbuf)
{
	FILE *fs = NULL;
	// check mp3 file
	if (typeflag != FTW_F) goto done;
	if (!has_mp3_ext(&fpath[ftwbuf->base])) goto done;
	// get file handle
	fs = fopen(fpath, "r");
	if (!fs) {
		warn(errno, "fopen()");
		goto done;
	}
	// get data bounds
	off_t start, end;
	if (mp3_data_bounds(fs, &start, &end)) {
		warn(0, "mp3_data_bounds() failed");
		goto done;
	}
	// create datahash object
	{
		Datahash dh;
		dh.name = string(fpath);
		assert(end >= start);
		dh.start = start;
		dh.end = end;
		memset(dh.md, '\0', DATAHASH_DIGEST_LENGTH);

		debugln("%llu ([%llu, %llu]) %s", dh.end - dh.start, start, end, dh.name.data());
		// add datahash object
		g_hashes.push_back(Datahash(dh));
	}
done:
	if (fs) fclose(fs);
	return 0;
}

int mp3_count_callback(
	const char *fpath,
	const struct stat *sb,
	int typeflag,
	struct FTW *ftwbuf)
{
	if (typeflag == FTW_SL) {
		warn(0, "Will not follow link: %s", fpath);
	}
	if (typeflag == FTW_F && has_mp3_ext(&fpath[ftwbuf->base])) {
		debugln(&fpath[ftwbuf->base]);
	}
	return 0;
}

void usage()
{
	puts("Invalid parameters specified.");
	puts("Correct usage: <program> <path>");
}

void get_size_matches(off_t *matches[], uint *matchcnt)
{
	size_t matchmax = 100;
	*matchcnt = 0;
	*matches = (off_t *)calloc(matchmax, sizeof(**matches));
	if (*matches == NULL) goto done;
	for (uint i = 0; i < g_hashes.size(); i++) {
		off_t size = g_hashes[i].end - g_hashes[i].start;
		// skip this size if it's already recorded
		uint match;
		for (match = 0; match < *matchcnt; match++) {
			if (size == (*matches)[match]) {
				break;
			}
		}
		assert(match >= 0 && match <= *matchcnt);
		if (match < *matchcnt) continue;
		// skip if no duplicates of this size found
		for (match = i + 1; match < g_hashes.size(); match++) {
			if (size == g_hashes[match].end - g_hashes[match].start) {
				break;
			}
		}
		assert(match >= i + 1 && match <= g_hashes.size());
		if (match == g_hashes.size()) continue;
		// add match
		assert(*matchcnt <= matchmax);
		if (*matchcnt == matchmax) {
			matchmax *= 2;
			assert(matchmax > *matchcnt);
			*matches = (off_t *)realloc(*matches, matchmax * sizeof(**matches));
			if (matches == NULL) fatal(errno, "realloc()");
		}
		(*matches)[(*matchcnt)++] = size;
	}
done:
	debugln("setting buffer size to %d bytes (%d items)", *matchcnt * sizeof(**matches), *matchcnt);
	off_t *resized = (off_t *)realloc(*matches, *matchcnt * sizeof(**matches));
	if (resized != NULL || *matchcnt * sizeof(**matches) == 0) *matches = resized;
	assert((!*matches && !*matchcnt) || (*matches && *matchcnt > 0));
}

int report_size_matches()
{
	off_t *matches;
	uint matchcnt;
	get_size_matches(&matches, &matchcnt);
	int filecnt = 0;
	for (uint match = 0; match < matchcnt; match++) {
		off_t size = matches[match];
		printf("\tMatches for data size = %llu:\n", size);
		for (uint i = 0; i < g_hashes.size(); i++) {
			if (size == g_hashes[i].end - g_hashes[i].start) {
				printf("%s\n", g_hashes[i].name.data());
				filecnt++;
			}
		}
	}
//done:
	if (matches != NULL) free(matches);
	return filecnt;
}

void add_size_match_hashes()
{
	off_t *matches;
	uint matchcnt;
	get_size_matches(&matches, &matchcnt);
	// hash all size matches
	for (uint match = 0; match < matchcnt; match++) {
		// find objects that match size
		for (uint i = 0; i < g_hashes.size(); i++) {
			if (g_hashes[i].end - g_hashes[i].start != matches[match]) continue;
			// check a hash isn't already created
			uint j;
			for (j = 0; j < sizeof(g_hashes[i].md); j++) {
				if (g_hashes[i].md[j] != '\0') {
					debugln("already hashed: %s", g_hashes[i].name.data());
					break;
				}
			}
			if (j != sizeof(g_hashes[i].md)) continue;
			// open file and add hash
			FILE *fsp = fopen(g_hashes[i].name.data(), "r");
			if (fsp == NULL) {
				warn(errno, "fopen()");
				continue;
			}
			if (sha1_mp3_data(fsp, g_hashes[i].md)) {
				warn(0, "sha1_mp3_data() failed");
			}
			fclose(fsp);
		}
	}
}

#ifndef NDEBUG
int hashmds_zeroed()
{
	for (uint i = 0; i < g_hashes.size(); i++) {
		for (uint j = 0; j < sizeof(g_hashes[i].md); j++) {
			if (g_hashes[i].md[j] != '\0') return 0;
		}
	}
	return 1;
}
#endif

int report_hash_matches()
{
	int filecnt = 0;
	for (uint i = 0; i < g_hashes.size(); i++) {
		// check file has a hash
		if (memnotchr(g_hashes[i].md, '\0', sizeof(g_hashes[i].md)) == NULL) {
			continue;
		}
#ifndef NDEBUG
		{
			uint j;
			for (j = 0; j < sizeof(g_hashes[i].md); j++) {
				if (g_hashes[i].md[j] != '\0') break;
			}
			assert(j != sizeof(g_hashes[i].md));
		}
#endif
		for (uint j = 0; j < g_hashes.size(); j++) {
			if (i == j) continue;
			if (memcmp(g_hashes[i].md, g_hashes[j].md, sizeof(g_hashes[i].md)) != 0) {
				continue;
			}
			if (j < i) break;
			assert(j != i);
			printf("\tMatches for hash = ");
			for (uint b = 0; b < sizeof(g_hashes[i].md); b++) {
				printf("%02x", g_hashes[i].md[b]);
			}
			puts(":");
			for (j = i; j < g_hashes.size(); j++) {
				if (!memcmp(g_hashes[i].md, g_hashes[j].md, sizeof(g_hashes[i].md))) {
					printf("%s\n", g_hashes[j].name.data());
					filecnt++;
				}
			}
			break;
		}
	}
	return filecnt;
}

void find_mp3_dupes(const char *dirname)
{
	// count mp3 files
	int n = nftw(dirname, mp3_count_callback, 10, FTW_PHYS);
	if (n) {
		if (n == -1) {
			warn(errno, "ftw() failed");
		} else {
			warn(errno, "mp3_count_callback() returned %d", n);
		}
		goto done;
	}
	// parse mp3 files
	n = nftw(dirname, parse_mp3_size_callback, 10, FTW_PHYS);
	if (n) {
		if (n == -1) {
			warn(errno, "ftw() failed");
		} else {
			warn(errno, "parse_mp3_size_callback() returned %d", n);
		}
		goto done;
	}
	assert(hashmds_zeroed());

	{
		int sizeMatchCount = report_size_matches();
		assert(hashmds_zeroed());
		add_size_match_hashes();
		if (sizeMatchCount > 0) putchar('\n');
		int hashMatchCount = report_hash_matches();
		if (hashMatchCount > 0) putchar('\n');
		printf("Found %u MP3 files\n", g_hashes.size());
		printf("Found %d size matches\n", sizeMatchCount);
		printf("Found %d hash matches\n", hashMatchCount);
	}

	g_hashes.clear();
	return;
done:
	exit(EXIT_FAILURE);
}

int main(int argc, char *argv[])
{
	int ret = EXIT_FAILURE;
	// do some checks
	debugln("system page size = %ld", sysconf(_SC_PAGESIZE));
	if (argc != 2) {
		usage();
		goto done;
	}
	debugln("argv[1] = %s", argv[1]);

	// parse mp3 files
	find_mp3_dupes(argv[1]);

	ret = EXIT_SUCCESS;
done:
	return ret;
}