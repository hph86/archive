from __future__ import division

from errno import *
from stat import *
from collections import namedtuple
from os import fpathconf, strerror, SEEK_SET, fstat
from os.path import split as path_split
import logging
logger = logging.getLogger("clfs")
del logging
from pdb import set_trace
import pprint
import struct
import time

CLFS_NAMEMAX = 255

clno_t = struct.Struct("I")

CLUSTER_VALUE_MAX = 0xffffffff
CLUSTER_FREE = 0
CLUSTER_END_OF_CHAIN = -1 & CLUSTER_VALUE_MAX
CLUSTER_ATAB_PADDING = -2 & CLUSTER_VALUE_MAX


class classproperty(property):
    """A normal property requires an instance, this one passes the owner (class) as the instance."""

    def __get__(self, instance, owner):
        return property.__get__(self, owner)


class ClfsStructType(type):

    # this is done in the new function so that changes to attrs
    # are made before class instantiation, and not hacked on later
    def __new__(mcs, name, bases, attrs):
        """Process _fields_ and finalize the fields attribute for the class"""
        fields = ()
        format = ""
        for a in bases:
            if name != "ClfsStruct" and issubclass(a, ClfsStruct):
                fields += a.fields
                format += a.format
        for a, b in attrs["_fields_"]:
            if a != None:
                fields += (a,)
            format += b
        attrs["_ClfsStruct__fields"] = fields
        attrs["_ClfsStruct__struct"] = struct.Struct(format)
        return type.__new__(mcs, name, bases, attrs)


class ClfsStruct(object):

    __metaclass__ = ClfsStructType # for the _fields_ processing
    _fields_ = () # for issubclass based processing

    def __init__(self, **initvals):
        self.__unpacked = {}
        for key, value in initvals.iteritems():
            self[key] = value

    #def __check_field(self, name):
        #if name not in (a.name for a in self.fields):
            #raise KeyError("{0} structure does not have field named {1!r}"
                    #.format(self.__class__.__name__, name))
    def __check_field(self, name):
        if name not in self.fields:
            raise KeyError(
                    "%s structure does not have field named %r"
                    % (self.__class__.__name__, name))

    def __getitem__(self, name):
        try:
            return self.__unpacked[name]
        except KeyError:
            pass
        self.__check_field(name)
        unpacked = self.__struct.unpack("\0" * self.__struct.size)
        assert len(unpacked) == len(self.fields)
        for field, value in zip(self.fields, unpacked):
            self.__unpacked.setdefault(field, value)
        return self.__unpacked[name]

    def __setitem__(self, name, value):
        if name not in self.__unpacked:
            self.__check_field(name)
        self.__unpacked[name] = value

    @classmethod
    def from_fileobj(class_, fileobj):
        """Unpacks a new instance of this struct from a file"""
        return class_.unpack(fileobj.read(class_.size))

    @classmethod
    def unpack(class_, buffer):
        """Unpacks the buffer according to this structs format and returns a new instance"""
        unpacked = class_.__struct.unpack(buffer)
        return class_(**dict(zip(class_.fields, unpacked)))

    def pack(self):
        values = []
        for field in self.fields:
            values.append(self[field])
        return self.__struct.pack(*values)

    @classproperty
    def format(self):
        return self.__struct.format

    @classproperty
    def fields(self):
        return self.__fields

    @classproperty
    def size(self):
        return self.__struct.size

    def __repr__(self):
        return "<{0} {1}>".format(self.__class__.__name__, zip(self.fields, self.__unpacked))


class DirEntry(ClfsStruct):

    _fields_ = (
            ("name", "{0}s".format(CLFS_NAMEMAX)),
            ("ino", "I"),)

#assert DirEntry.size == 256, DirEntry.size


class Inode(ClfsStruct):

    _fields_ = (
            ("mode", "I"),
#            (None, "4x"),
            ("size", "Q"),
            ("nlink", "i"),
            ("uid", "I"),
            ("gid", "I"),
            ("rdev", "Q"),
            ("atime", "I"),
            ("atimens", "I"),
            ("mtime", "I"),
            ("mtimens", "I"),
            ("ctime", "I"),
            ("ctimens", "I"),
            #(None, "202x"),
        )

    def get_st_times(self):
        return dict((
                    ("st_" + a, self[a] + self[a + "ns"] / (10 ** 9))
                    for a in (b + "time" for b in "amc")))

    def posix_inode_created(self):
        # see open(3), mknod(3), mkdir(3)
        time_parts = time_as_posix_spec(time.time())
        for time_char in "amc":
            self[time_char + "time"], self[time_char + "timens"] = time_parts

    def set_time_fields(self, newtime=None, access=False, modify=False, change=False):
        if newtime is None:
            newtime = time.time()
        time_parts = time_as_posix_spec(newtime)
        for prefix, update in (("a", access), ("m", modify), ("c", change)):
            if update:
                self[prefix + "time"], self[prefix + "timens"] = time_parts

    def posix_inode_changed(self):
        # see write(3)
        self.set_time_fields(modify=True, change=True)
        if (S_ISREG(self["mode"])):
            self["mode"] &= ~S_ISGID & ~S_ISUID

    def posix_inode_accessed(self):
        # see read(3)
        self.set_time_fields(access=True)

    def posix_inode_status_changed(self):
        # see chmod(3)
        self.set_time_fields(change=True)

#assert Inode.size == 256, Inode.size


class BootRecord(ClfsStruct):

    _fields_ = (
            ("ident", "8s"),
            ("version", "I"),
            ("clrsize", "I"),
            ("mstrclrs", "I"),
            ("atabclrs", "I"),
            ("dataclrs", "I"),
            #(None, "228x"),
        )

#assert BootRecord.size == 256
#assert BootRecord.size <= ROOT_DIRENT_CLUSTER_OFFSET


class ClfsError(OSError):

    def __init__(self, errno):
        OSError.__init__(self, errno, strerror(errno))


def time_as_posix_spec(time):
    return int(time), int((time % 1) * (10 ** 9))


class Clfs(object):

    def __init__(self, path):

        self._f = open(path, "r+b")
        self._path_cache = {}
        self._path_max = fpathconf(self._f.fileno(), "PC_PATH_MAX")
        logger.info("PATH_MAX is %i", self._path_max)

        br = BootRecord.from_fileobj(self._f)
        assert br["ident"].rstrip("\0") == "clfs", repr(br["ident"])
        assert br["version"] == 1

        self.cluster_size = br["clrsize"]
        self.clno_count_per_atab_cluster = self.cluster_size // clno_t.size

        self.region_master_start = 0
        self.region_master_length = br["mstrclrs"]
        self.region_master_end = self.region_master_start + self.region_master_length

        self.region_atab_start = self.region_master_end
        self.region_atab_length = br["atabclrs"]
        self.region_atab_end = self.region_atab_start + self.region_atab_length

        self.region_data_start = self.region_atab_end
        self.region_data_length = br["dataclrs"]
        self.region_data_end = self.region_atab_start + self.region_data_length

        self.fs_cluster_count = \
                self.region_master_length \
                + self.region_atab_length \
                + self.region_data_length
        self.fs_total_byte_size = self.fs_cluster_count * self.cluster_size

        self._last_alloc_clno = self.region_data_end - 1

    # --- device functions

    def safe_seek(self, offset, whence=SEEK_SET):
        self._f.seek(offset)
        assert self._f.tell() < self.fs_total_byte_size

    def device_flush(self):
        #self._f.flush()
        pass

    # --- cluster functions

    def cluster_seek(self, cluster, offset=0):
        assert cluster < self.fs_cluster_count, cluster
        assert offset < self.cluster_size, offset
        byte_offset = cluster * self.cluster_size + offset
        self.safe_seek(byte_offset)

    def cluster_read(self, cluster, offset, size):
        self.cluster_seek(cluster, offset)
        assert size + offset <= self.cluster_size
        logger.debug("Reading %i:[%i,%i), %i bytes", cluster, offset, offset + size, size)
        return self._f.read(size)

    def cluster_write(self, cluster, offset, buffer):
        self.cluster_seek(cluster, offset)
        endoff = offset + len(buffer)
        assert endoff <= self.cluster_size, endoff
        assert self._f.tell() == cluster * self.cluster_size + offset
        logger.debug("Writing %i:[%i,%i), %i bytes",
                cluster, offset, endoff, len(buffer))
        self._f.write(buffer)

    def cluster_get_next(self, clno):
        self.cluster_seek_next(clno)
        return clno_t.unpack(self._f.read(clno_t.size))[0]

    def atab_iter(self):
        self.cluster_seek(self.region_atab_start)
        for atab_cluster in xrange(
                self.region_atab_start,
                self.region_atab_end):
            buffer = self.cluster_read(atab_cluster, 0, self.cluster_size)
            assert len(buffer) == self.cluster_size, len(buffer)
            for offset in xrange(0, self.cluster_size, clno_t.size):
                yield clno_t.unpack_from(buffer, offset)[0]

    def cluster_alloc(self):
        clno = self._last_alloc_clno
        assert self.region_data_start <= clno < self.region_data_end, clno
        while True:
            clno += 1
            if clno >= self.region_data_end:
                clno = self.region_data_start
            next_clno = self.cluster_get_next(clno)
            if next_clno == CLUSTER_FREE:
                self.cluster_set_next(clno, CLUSTER_END_OF_CHAIN)
                self._last_alloc_clno = clno
                logger.debug("Allocated data cluster %i", clno)
                return clno
            if clno == self._last_alloc_clno:
                raise ClfsError(ENOSPC)

    def cluster_seek_next(self, clno):
        # witness the power of python operation precedence
        assert self.region_data_start <= clno < self.region_data_end, clno
        atab_i = clno - self.region_data_start
        cluster = self.region_atab_start + atab_i // self.clno_count_per_atab_cluster
        offset = atab_i % self.clno_count_per_atab_cluster * clno_t.size
        self.cluster_seek(cluster, offset)

    def cluster_set_next(self, clno, next):
        self.cluster_seek_next(clno)
        logger.debug("Setting cluster number %i->%i", clno, next)
        self._f.write(clno_t.pack(next))
        assert self.cluster_get_next(clno) == next, (clno, next)

    def atab_clear(self):
        self.cluster_seek(self.region_atab_start)
        # set all clnos as free
        for index in xrange(self.region_data_length):
            self._f.write(clno_t.pack(CLUSTER_FREE))
        # set the overhang as padding
        for index in xrange(
                self.clno_count_per_atab_cluster - \
                (self.region_data_length % self.clno_count_per_atab_cluster)):
            self._f.write(clno_t.pack(CLUSTER_ATAB_PADDING))
        assert self._f.tell() == self.cluster_size * self.region_atab_end

    def dir_remove(self, ino, offset, inode):
        """ino and inode are for the parent directory, offset is for the entry"""
        assert offset + DirEntry.size <= inode["size"]
        self.node_data_write(ino, offset, DirEntry().pack(), inode)
        #inode.posix_inode_changed()

    def ino_for_root(self):
        return self.region_data_start

    def inode_read(self, ino):
        logger.debug("Reading inode %i", ino)
        return Inode.unpack(self.cluster_read(ino, 0, Inode.size))

    def chain_shorten(self, clno, size):
        while True:
            next = self.cluster_get_next(clno)
            if next == CLUSTER_FREE:
                logger.error("Chain contains unallocated cluster %i", clno)
            if size > self.cluster_size:
                if next == CLUSTER_END_OF_CHAIN:
                    logger.error("Chain ends prematurely, expected %i more bytes", size)
                    break
            elif 0 < size <= self.cluster_size:
                self.cluster_set_next(clno, CLUSTER_END_OF_CHAIN)
                logger.debug("Marking cluster %i as end of chain", clno)
            elif next != CLUSTER_FREE:
                logger.debug("Freeing cluster %i", clno)
                self.cluster_set_next(clno, CLUSTER_FREE)
            if next in (CLUSTER_FREE, CLUSTER_END_OF_CHAIN):
                break
            clno = next
            size -= self.cluster_size

    def _truncate(self, ino, size):
        inode = self.inode_read(ino)
        if S_ISDIR(inode["mode"]):
            raise ClfsError(EISDIR)
        else: # what about non regular file types?
            pass
        if size == inode["size"]:
            return
        elif size > inode["size"]:
            self.node_data_write(
                    ino=ino,
                    offset=inode["size"], #end of file
                    buffer="\0" * (size - inode["size"]), # all zeros
                    inode=inode)
        elif size < inode["size"]:
            self.chain_shorten(ino, size + inode.size)
            # since we used chain_shorten we need to manually update the inode
            inode["size"] = size
            inode.posix_inode_changed()
        self.inode_write(ino, inode)
        assert self.inode_read(ino)["size"] == size

    def os_chmod(self, path, mode):
        logger.debug("os_chmod(%r, %o)", path, mode)
        ino = self.ino_from_path(path)
        inode = self.inode_read(ino)
        assert S_IFMT(mode) == S_IFMT(inode["mode"])
        inode["mode"] = mode
        inode.posix_inode_status_changed()
        self.inode_write(ino, inode)

    def os_chown(self, path, uid, gid):
        ino = self.ino_from_path(path)
        if uid == -1 and gid == -1:
            return
        inode = self.inode_read(ino)
        if uid != -1:
            inode["uid"] = uid
        if gid != -1:
            inode["gid"] = gid
        if inode["mode"] & (S_IXUSR|S_IXGRP|S_IXOTH):
            inode["mode"] &= ~(S_ISUID|S_ISGID)
        inode.set_time_fields(change=True)
        self.inode_write(ino, inode)

    def os_rmdir(self, path):
        dirpath, name = path_split(path)
        dirino = self.ino_for_path(dirpath)
        self._rmdir(dirino, name)
        del self._path_cache[path]

    def os_truncate(self, path, size):
        self._truncate(self.ino_from_path(path), size)

    def os_unlink(self, path):
        dirpath, name = path_split(path)
        dirino = self.ino_for_path(dirpath)
        self._unlink(dirino, name)
        del self._path_cache[path]

    def os_utimens(self, path, times):
        self._utimens(self.ino_for_path(path), times)

    def os_readlink(self, path):
        ino = self.ino_for_path(path)
        inode = self.inode_read(ino)
        if not S_ISLNK(inode["mode"]):
            raise ClfsError(EINVAL)
        buf = self.node_data_read(ino, 0, inode["size"], inode)
        self.inode_write(ino, inode)
        return buf

    def os_rename(self, old, new):
        oldpdir, oldname = path_split(old)
        oldpino = self.ino_for_path(oldpdir)
        newpdir, newname = path_split(new)
        newpino = self.ino_for_path(newpdir)
        self._rename(oldpino, oldname, newpino, newname)

    def os_symlink(self, target, source):
        logger.debug("os_symlink(%r, %r)", target, source)
        self.node_create(target, S_IFLNK|0777, 0, 0, 0)
        ino = self.ino_for_path(target)
        inode = self.inode_read(ino)
        self.node_data_write(
                self.ino_for_path(target),
                0,
                source,
                inode)
        self.inode_write(ino, inode)

    def _rename(self, oldpino, oldname, newpino, newname):
        oldoff, olddire = self._scandir(oldpino, oldname)
        ino = olddire["ino"]
        inode = self.inode_read(ino)
        try:
            newoff, newdire = self._scandir(newpino, newname)
        except ClfsError as exc:
            if exc.errno != ENOENT:
                raise
        else:
            if S_ISDIR(inode["mode"]):
                self._rmdir(newpino, newname)
            else:
                self._unlink(newpino, newname)
        oldpinode = self.inode_read(oldpino)
        self.dir_remove(oldpino, oldoff, oldpinode)
        self.inode_write(oldpino, oldpinode)
        newpinode = self.inode_read(newpino)
        newdire = DirEntry(name=newname, ino=ino)
        self.dir_add(newpino, newpinode, newdire, inode)
        self.inode_write(newpino, newpinode)

    def _rmdir(self, pino, name):
        offset, dirent = self._scandir(pino, name)
        ino = dirent["ino"]
        inode = self.inode_read(ino)
        if not S_ISDIR(inode["mode"]):
            raise ClfsError(ENOTDIR)
        pinode = self.inode_read(pino)
        self.node_unlink(pino, pinode, offset, ino, inode)
        self.inode_write(pino, pinode)

    # inode is invalid after this function
    def node_unlink(self, pino, pinode, diroff, ino, inode):
        if S_ISDIR(inode["mode"]):
            if inode["nlink"] < 2:
                logger.error(
                        "Directory inode %i has invalid link count %i",
                        ino, inode["nlink"])
            if inode["nlink"] > 2:
                raise ClfsError(ENOTEMPTY)
            else: # check there aren't files
                try:
                    self.dir_iter(ino, inode).next()
                except StopIteration:
                    pass
                else:
                    raise ClfsError(ENOTEMPTY)
            pinode["nlink"] -= 1
        else:
            inode["nlink"] -= 1
            if inode["nlink"] < 0:
                logger.error("Inode %i has invalid link count %i",
                        ino, inode["nlink"])
        self.dir_remove(pino, diroff, pinode)
        if S_ISDIR(inode["mode"]) or inode["nlink"] <= 0:
            self.inode_write(ino, Inode())
            self.chain_shorten(ino, 0)

    def _unlink(self, dirino, name):
        offset, dirent = self._scandir(dirino, name)
        ino = dirent["ino"]
        inode = self.inode_read(dirent["ino"])
        if S_ISDIR(inode["mode"]):
            raise ClfsError(EISDIR)
        dirinode = self.inode_read(dirino)
        self.node_unlink(dirino, dirinode, offset, ino, inode)
        self.inode_write(dirino, dirinode)

    def _scandir(self, ino, name):
        if len(name) > CLFS_NAMEMAX:
            raise ClfsError(ENAMETOOLONG)
        for offset, dirent in self._readdir(ino):
            if dirent["name"].rstrip("\0") == name:
                return offset, dirent
        else:
            raise ClfsError(ENOENT)

    def _readdir(self, ino):
        return self.dir_iter(ino, self.inode_read(ino))

    def ino_for_path(self, path):
        logger.debug("ino_for_path(%r)", path)

        # null byte must be included against path max see limits.h(7posix)
        if len(path) + 1 > self._path_max:
            raise ClfsError(ENAMETOOLONG)

        if path == "/":
            ino = self.ino_for_root()
        else:
            pathbits = path.split("/")
            # first pathbit is empty, since we expect a leading "/"
            assert not pathbits[0]
            assert all(pathbits[1:]), path

            # look for gradually widening path prefix to begin
            # less than 2 pathbits doesn't make sense, root should have caught this
            for index in xrange(len(pathbits), 1, -1):
                key = "/".join(pathbits[:index])
                logger.debug("Checking for cached path: %r", key)
                try:
                    ino = self._path_cache[key]
                except KeyError:
                    pass
                else:
                    logger.debug("Cached path %r has ino %i", key, ino)
                    break
            else:
                # if nothing was found, start with the root
                ino = self.ino_for_root()
                index = 1

            # do the rest of the searches from the disk
            for index, name in enumerate(pathbits[index:], index):
                logger.debug("Finding %r in dir inode %i", name, ino)
                ino = self._scandir(ino, name)[1]["ino"]
                subpath = "/".join(pathbits[:index+1])
                # we wouldn't be doing this if it was in the cache
                assert subpath not in self._path_cache
                self._path_cache[subpath] = ino
                logger.debug("Caching path ino %r: %i", subpath, ino)
        logger.debug("ino_for_path->%i", ino)
        return ino
    ino_from_path = ino_for_path

    def chain_read(self, clno, offset, read_size, chain_size):
        buf = ""
        while True:
            assert offset >= 0, offset
            assert read_size >= 0, read_size
            assert chain_size >= 0, chain_size
            if offset < self.cluster_size:
                read_now = min(
                        read_size, # we wouldn't more than this
                        self.cluster_size - offset, # how much remains on the cluster
                        chain_size - offset) # how much remains in the chain
                buf += self.cluster_read(clno, offset, read_now)
                read_size -= read_now
                offset += read_now
            if read_size == 0 or chain_size <= self.cluster_size:
                break
            next_clno = self.cluster_get_next(clno)
            if next_clno == CLUSTER_END_OF_CHAIN:
                break
            clno = next_clno
            offset -= self.cluster_size
            chain_size -= self.cluster_size
        return buf

    def chain_write(self, clno, offset, buffer):
        while True:
            if offset < self.cluster_size:
                write_here = min(len(buffer), self.cluster_size - offset)
                self.cluster_write(clno, offset, buffer[:write_here])
                buffer = buffer[write_here:]
                offset += write_here
            if len(buffer) == 0:
                return
            next_clno = self.cluster_get_next(clno)
            if next_clno == CLUSTER_END_OF_CHAIN:
                next_clno = self.cluster_alloc()
                self.cluster_set_next(clno, next_clno)
            clno = next_clno
            offset -= self.cluster_size

    def node_data_write(self, ino, offset, buffer, inode):
        data_offset = inode.size
        self.chain_write(ino, offset + data_offset, buffer)
        inode["size"] = max(offset + len(buffer), inode["size"])
        inode.posix_inode_changed()

    def _read(self, ino, offset, size):
        inode = self.inode_read(ino)
        buf = self.node_data_read(ino, offset, size, inode)
        if len(buf) > 0:
            inode.posix_inode_accessed()
            self.inode_write(ino, inode)
        return buf

    def _write(self, ino, offset, buf):
        inode = self.inode_read(ino)
        self.node_data_write(ino, offset, buf, inode)
        self.inode_write(ino, inode)

    def node_data_read(self, ino, offset, size, inode):
        data_offset = inode.size
        return self.chain_read(
                clno=ino,
                chain_size=inode["size"] + data_offset,
                offset=offset + data_offset,
                read_size=size)

    def inode_write(self, ino, inode):
        self.chain_write(ino, 0, inode.pack())

    def _utimens(self, ino, times):
        inode = self.inode_read(ino)
        inode.set_time_fields(times[0], access=True)
        inode.set_time_fields(times[1], modify=True)
        self.inode_write(ino, inode)

    def inode_new(self, mode, uid, gid, rdev=0):
        inode = Inode(size=0, uid=uid, gid=gid, mode=mode, rdev=rdev)
        inode.posix_inode_created()
        if S_ISDIR(mode):
            inode["nlink"] = 2
        else:
            inode["nlink"] = 1
        return inode

    def root_create(self, mode, uid, gid, rdev=0):
        logger.debug("root_create(%o, %i, %i, %x)", mode, uid, gid, rdev)
        # can the root be something other than a directory?
        inode = self.inode_new(mode, uid, gid, rdev)
        dirent = DirEntry(ino=self.cluster_alloc(), name="/")
        assert dirent["ino"] == self.region_data_start
        self.inode_write(dirent["ino"], inode)
        self.cluster_write(self.region_master_start, BootRecord.size, dirent.pack())

    def os_mkdir(self, path, mode, uid, gid):
        ppath, name = path_split(path)
        pino = self.ino_for_path(ppath)
        self.node_create(pino, name, S_IFDIR|mode, uid, gid)

    def os_mknod(self, path, mode, uid, gid, rdev):
        ppath, name = path_split(path)
        pino = self.ino_for_path(ppath)
        self.node_create(pino, name, mode, uid, gid, rdev)

    def node_create(self, parent_ino, name, mode, uid, gid, rdev=0):
        """Create an allocate a new inode, update relevant structures elsewhere"""
        logger.debug(
                "node_create(%i, %r, %o, %i, %i, %x)",
                parent_ino, name, mode, uid, gid, rdev)

        parent_inode = self.inode_read(parent_ino)
        if self.dir_exists(parent_ino, parent_inode, name):
            raise ClfsError(EEXIST)

        new_inode = self.inode_new(mode, uid, gid, rdev)
        new_dirent = DirEntry(ino=self.cluster_alloc(), name=name)

        # write inode
        self.inode_write(new_dirent["ino"], new_inode)

        self.dir_add(parent_ino, parent_inode, new_dirent, new_inode)
        # update parent inode
        self.inode_write(parent_ino, parent_inode)

    def dir_add(self, dino, dinode, dirent, inode):
        assert S_ISDIR(dinode["mode"])
        if S_ISDIR(inode["mode"]):
            # linux add link for subdirs, other platforms differ
            dinode["nlink"] += 1
        # write the new entry to the end of the directory data
        self.node_data_write(
                ino=dino,
                offset=dinode["size"],
                buffer=dirent.pack(),
                inode=dinode)

    def dir_exists(self, dino, dinode, name):
        for offset, dirent in self.dir_iter(dino, dinode):
            if dirent["name"].rstrip("\0") == name:
                return True
        else:
            return False

    def dir_iter(self, ino, inode):
        if not S_ISDIR(inode["mode"]):
            raise ClfsError(ENOTDIR)
        offset = 0
        while offset < inode["size"]:
            dirent = DirEntry.unpack(self.node_data_read(
                    ino,
                    offset,
                    DirEntry.size,
                    inode))
            if dirent["name"][0] != "\0":
                yield offset, dirent
            offset += dirent.size
        assert offset == inode["size"]

def generate_bootrecord(device_size):
    # some basic geometry
    cluster_size = 512
    cluster_number_bits = 8 * clno_t.size
    device_cluster_count = device_size // cluster_size

    # determine region allocations
    master_region_cluster_count = 1
    allocation_table_cluster_count = 0
    data_region_cluster_count = 0
    unallocated_cluster_count = device_cluster_count - master_region_cluster_count
    assert unallocated_cluster_count >= 0, "No space for master region"
    cluster_numbers_per_allocation_table_cluster = \
        (cluster_size * 8) // cluster_number_bits
    print "clusters per allocation table cluster", \
        cluster_numbers_per_allocation_table_cluster
    while unallocated_cluster_count > 0:
        allocation_table_cluster_count += 1
        unallocated_cluster_count -= 1
        assigned_cluster_number_count = min(
                cluster_numbers_per_allocation_table_cluster,
                unallocated_cluster_count)
        data_region_cluster_count += assigned_cluster_number_count
        unallocated_cluster_count -= assigned_cluster_number_count
    del assigned_cluster_number_count
    del unallocated_cluster_count

    # report some of the decisions made
    filesystem_cluster_count = \
            master_region_cluster_count + \
            allocation_table_cluster_count + \
            data_region_cluster_count
    print "master region cluster count", master_region_cluster_count
    print "allocation table cluster count", allocation_table_cluster_count
    print "data region cluster count", data_region_cluster_count
    assert filesystem_cluster_count == device_cluster_count, \
        (filesystem_cluster_count, device_cluster_count)

    br = BootRecord()
    br["clrsize"] = cluster_size
    br["mstrclrs"] = master_region_cluster_count
    br["atabclrs"] = allocation_table_cluster_count
    br["dataclrs"] = data_region_cluster_count
    br["ident"] = "clfs"
    br["version"] = 1
    return br

def create_filesystem(path):
    # create and write boot record
    f = open(path, "r+b")
    br = generate_bootrecord(fstat(f.fileno()).st_size)
    f.seek(0)
    #assert br.size <= ROOT_DIRENT_CLUSTER_OFFSET
    f.write(br.pack())
    f.close()

    fs = Clfs(path)
    fs.atab_clear()
    fs.root_create(mode=S_IFDIR|0777, uid=0, gid=0)
    root_next = fs.cluster_get_next(fs.ino_for_root())
    assert root_next == CLUSTER_END_OF_CHAIN, root_next
