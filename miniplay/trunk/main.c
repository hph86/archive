#include "miniplay.h"

gint main(gint argc, gchar *argv[])
{
	gtk_init(&argc, &argv);
	gst_init(&argc, &argv);
	g_set_prgname("Miniplayer");
	init_audio();
	init_tray();
	select_music();
	connect_tray_signals();
	gtk_main();
	return 0;
}
