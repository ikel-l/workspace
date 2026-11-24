#include "config.h"

#include "spicy-theme.h"

#include <math.h>
#include <string.h>

#ifdef G_OS_WIN32
#include <windows.h>
#endif

#ifdef SPICY_HAVE_FONTCONFIG
#include <fontconfig/fontconfig.h>
#endif

#ifdef SPICY_HAVE_CORETEXT
#define __ASSERT_MACROS_DEFINE_VERSIONS_WITHOUT_UNDERSCORES 0
#include <CoreFoundation/CoreFoundation.h>
#include <CoreText/CoreText.h>
#endif

#ifdef __APPLE__
#include <mach-o/dyld.h>
#include <limits.h>
#include <stdlib.h>
#endif

static const gchar spicy_theme_css[] =
    ".gvs-panel {"
    "  background-color: #1e293b;"
    "  background-image: none;"
    "  border: 1px solid #334155;"
    "  border-top-width: 0;"
    "  border-radius: 0 0 13px 13px;"
    "  box-shadow: 0 6px 16px rgba(0, 0, 0, 0.30), 0 1px 3px rgba(0, 0, 0, 0.25);"
    "  padding: 8px 10px;"
    "}"

    ".gvs-panel button {"
    "  font-family: Montserrat, 'Segoe UI', Cantarell, sans-serif;"
    "  font-weight: 500;"
    "  font-size: 13px;"
    "  color: #f1f5f9;"
    "  background-color: transparent;"
    "  background-image: none;"
    "  border: 1px solid transparent;"
    "  border-radius: 9px;"
    "  box-shadow: none;"
    "  text-shadow: none;"
    "  -gtk-icon-shadow: none;"
    "  outline-color: rgba(47, 178, 214, 0.6);"
    "  padding: 6px 12px 6px 10px;"
    "  min-height: 22px;"
    "  transition: background-color 120ms ease-out, border-color 120ms ease-out;"
    "}"
    ".gvs-panel button:hover {"
    "  background-color: #273449;"
    "  border-color: #334155;"
    "}"
    ".gvs-panel button:active,"
    ".gvs-panel button.gvs-open {"
    "  background-color: #334155;"
    "  border-color: #475569;"
    "}"
    ".gvs-panel button .gvs-chevron {"
    "  color: #94a3b8;"
    "}"

    ".gvs-panel button.gvs-danger {"
    "  color: #f87171;"
    "  background-color: rgba(248, 113, 113, 0.10);"
    "  border-color: rgba(248, 113, 113, 0.22);"
    "}"
    ".gvs-panel button.gvs-danger:hover {"
    "  color: #fca5a5;"
    "  background-color: rgba(248, 113, 113, 0.20);"
    "  border-color: rgba(248, 113, 113, 0.40);"
    "}"
    ".gvs-panel button.gvs-danger:active {"
    "  background-color: rgba(248, 113, 113, 0.30);"
    "}"

    ".gvs-separator {"
    "  background-color: #334155;"
    "  background-image: none;"
    "  min-width: 1px;"
    "  margin: 6px 6px;"
    "}"

    ".gvs-grip {"
    "  background-color: rgba(241, 245, 249, 0.80);"
    "  border-radius: 0 0 4px 4px;"
    "  box-shadow: 0 1px 3px rgba(0, 0, 0, 0.45);"
    "  min-width: 48px;"
    "  min-height: 4px;"
    "}"

    "window.gvs-menu-window,"
    "window.gvs-menu-window decoration {"
    "  background-color: #1e293b;"
    "  box-shadow: none;"
    "  border-radius: 0;"
    "}"
    "menu.gvs-menu {"
    "  font-family: Montserrat, 'Segoe UI', Cantarell, sans-serif;"
    "  font-weight: 500;"
    "  font-size: 13px;"
    "  color: #f1f5f9;"
    "  background-color: #1e293b;"
    "  background-image: none;"
    "  border: 1px solid #334155;"
    "  padding: 6px;"
    "}"
    "menu.gvs-menu.gvs-rounded {"
    "  border-radius: 13px;"
    "}"
    "menu.gvs-menu menuitem {"
    "  color: #f1f5f9;"
    "  background-color: transparent;"
    "  background-image: none;"
    "  border-radius: 9px;"
    "  padding: 7px 12px;"
    "  min-height: 20px;"
    "  text-shadow: none;"
    "}"
    "menu.gvs-menu menuitem:hover {"
    "  color: #f1f5f9;"
    "  background-color: #273449;"
    "}"
    "menu.gvs-menu menuitem:disabled,"
    "menu.gvs-menu menuitem:disabled label {"
    "  color: #475569;"
    "}"
    "menu.gvs-menu menuitem arrow {"
    "  color: #94a3b8;"
    "  min-width: 16px;"
    "  min-height: 16px;"
    "}"
    "menu.gvs-menu separator {"
    "  background-color: #334155;"
    "  min-height: 1px;"
    "  margin: 5px 6px;"
    "}"
    "menu.gvs-menu menuitem check,"
    "menu.gvs-menu menuitem radio {"
    "  color: transparent;"
    "  background-color: #273449;"
    "  background-image: none;"
    "  border: 1px solid #475569;"
    "  box-shadow: none;"
    "  -gtk-icon-shadow: none;"
    "  min-width: 14px;"
    "  min-height: 14px;"
    "  margin: 0 10px 0 0;"
    "  border-radius: 5px;"
    "}"
    "menu.gvs-menu menuitem radio {"
    "  border-radius: 100%;"
    "}"
    "menu.gvs-menu menuitem check:checked,"
    "menu.gvs-menu menuitem radio:checked {"
    "  color: #0f172a;"
    "  background-color: #2fb2d6;"
    "  border-color: #2fb2d6;"
    "}"
    "menu.gvs-menu menuitem check:disabled,"
    "menu.gvs-menu menuitem radio:disabled {"
    "  background-color: transparent;"
    "  border-color: #334155;"
    "}"
    "menu.gvs-menu menuitem check:checked:disabled,"
    "menu.gvs-menu menuitem radio:checked:disabled {"
    "  color: #94a3b8;"
    "  background-color: #334155;"
    "}";


#define SPICY_FONTS_SUBDIR "gorizont-spicy" G_DIR_SEPARATOR_S "fonts"

static const gchar *const spicy_font_files[] = {
    "Montserrat-Medium.ttf",
    "Montserrat-SemiBold.ttf",
};

static gboolean spicy_theme_add_font(const gchar *path)
{
#if defined(G_OS_WIN32)
    gunichar2 *wide = g_utf8_to_utf16(path, -1, NULL, NULL, NULL);
    gboolean added = wide != NULL && AddFontResourceExW(wide, FR_PRIVATE, 0) > 0;
    g_free(wide);
    return added;
#elif defined(SPICY_HAVE_CORETEXT)
    CFURLRef url = CFURLCreateFromFileSystemRepresentation(NULL, (const UInt8 *)path,
                                                           (CFIndex)strlen(path), false);
    CFErrorRef error = NULL;
    gboolean added = FALSE;

    if (url == NULL)
        return FALSE;
    added = CTFontManagerRegisterFontsForURL(url, kCTFontManagerScopeProcess, &error) ? TRUE : FALSE;
    if (error)
        CFRelease(error);
    CFRelease(url);
    return added;
#elif defined(SPICY_HAVE_FONTCONFIG)
    return FcConfigAppFontAddFile(FcConfigGetCurrent(), (const FcChar8 *)path);
#else
    (void)path;
    return FALSE;
#endif
}

static gchar *spicy_theme_exe_dir(void)
{
#if defined(G_OS_WIN32)
    wchar_t buffer[MAX_PATH];
    DWORD length = GetModuleFileNameW(NULL, buffer, G_N_ELEMENTS(buffer));
    if (length == 0 || length >= G_N_ELEMENTS(buffer))
        return NULL;
    gchar *exe = g_utf16_to_utf8(buffer, length, NULL, NULL, NULL);
    gchar *dir = exe ? g_path_get_dirname(exe) : NULL;
    g_free(exe);
    return dir;
#elif defined(__APPLE__)
    char raw[PATH_MAX], real[PATH_MAX];
    uint32_t size = sizeof(raw);
    if (_NSGetExecutablePath(raw, &size) != 0 || realpath(raw, real) == NULL)
        return NULL;
    return g_path_get_dirname(real);
#elif defined(__linux__)
    gchar *exe = g_file_read_link("/proc/self/exe", NULL);
    gchar *dir = exe ? g_path_get_dirname(exe) : NULL;
    g_free(exe);
    return dir;
#else
    return NULL;
#endif
}

void spicy_theme_load_fonts(void)
{
    static gboolean loaded = FALSE;
    GPtrArray *dirs;
    gchar *exe_dir;
    guint i, j;

    if (loaded)
        return;
    loaded = TRUE;

    dirs = g_ptr_array_new_with_free_func(g_free);

    if (g_getenv("GORIZONT_SPICY_FONTS_DIR"))
        g_ptr_array_add(dirs, g_strdup(g_getenv("GORIZONT_SPICY_FONTS_DIR")));

    exe_dir = spicy_theme_exe_dir();
    if (exe_dir) {
        g_ptr_array_add(dirs, g_build_filename(exe_dir, "..", "share", SPICY_FONTS_SUBDIR, NULL));
        g_ptr_array_add(dirs, g_build_filename(exe_dir, "fonts", NULL));
#ifdef __APPLE__
        g_ptr_array_add(dirs, g_build_filename(exe_dir, "..", "Resources", "spice", "share",
                                               SPICY_FONTS_SUBDIR, NULL));
#endif
        g_free(exe_dir);
    }

#ifdef SPICY_FONTS_DIR
    g_ptr_array_add(dirs, g_strdup(SPICY_FONTS_DIR));
#endif

    for (i = 0; i < dirs->len; i++) {
        const gchar *dir = g_ptr_array_index(dirs, i);
        gboolean found = FALSE;

        for (j = 0; j < G_N_ELEMENTS(spicy_font_files); j++) {
            gchar *path = g_build_filename(dir, spicy_font_files[j], NULL);
            if (g_file_test(path, G_FILE_TEST_IS_REGULAR)) {
                found = TRUE;
                if (!spicy_theme_add_font(path))
                    g_debug("spicy: could not load the font %s", path);
            }
            g_free(path);
        }
        if (found)
            break;
    }

    g_ptr_array_unref(dirs);
}

void spicy_theme_init(void)
{
    static gboolean inited = FALSE;
    GtkCssProvider *provider;
    GError *error = NULL;

    if (inited)
        return;
    inited = TRUE;

    provider = gtk_css_provider_new();
    if (!gtk_css_provider_load_from_data(provider, spicy_theme_css, -1, &error)) {
        g_warning("spicy: the style of the panel is broken: %s", error->message);
        g_clear_error(&error);
    }
    gtk_style_context_add_provider_for_screen(gdk_screen_get_default(),
                                              GTK_STYLE_PROVIDER(provider),
                                              GTK_STYLE_PROVIDER_PRIORITY_APPLICATION);
    g_object_unref(provider);
}


#define SPICY_ICON_CHEVRON (-1)
#define SPICY_ICON_SIZE 18

static void spicy_theme_icon_path(cairo_t *cr, gint icon)
{
    switch (icon) {
    case SPICY_ICON_POWER:
        cairo_new_sub_path(cr);
        cairo_arc(cr, 12, 13, 7.5, -G_PI / 2 + 0.75, 3 * G_PI / 2 - 0.75);
        cairo_stroke(cr);
        cairo_move_to(cr, 12, 3);
        cairo_line_to(cr, 12, 11.5);
        cairo_stroke(cr);
        break;

    case SPICY_ICON_EXIT_FULLSCREEN:
        cairo_move_to(cr, 9, 3.5);
        cairo_line_to(cr, 9, 9);
        cairo_line_to(cr, 3.5, 9);
        cairo_move_to(cr, 15, 3.5);
        cairo_line_to(cr, 15, 9);
        cairo_line_to(cr, 20.5, 9);
        cairo_move_to(cr, 3.5, 15);
        cairo_line_to(cr, 9, 15);
        cairo_line_to(cr, 9, 20.5);
        cairo_move_to(cr, 20.5, 15);
        cairo_line_to(cr, 15, 15);
        cairo_line_to(cr, 15, 20.5);
        cairo_stroke(cr);
        break;

    case SPICY_ICON_OPTIONS: {
        static const double rows[3][2] = { { 6.5, 15 }, { 12, 8.5 }, { 17.5, 13.5 } };
        int i;
        for (i = 0; i < 3; i++) {
            double y = rows[i][0], knob = rows[i][1];
            cairo_move_to(cr, 3.5, y);
            cairo_line_to(cr, knob - 2.6, y);
            cairo_move_to(cr, knob + 2.6, y);
            cairo_line_to(cr, 20.5, y);
            cairo_stroke(cr);
            cairo_new_sub_path(cr);
            cairo_arc(cr, knob, y, 2.4, 0, 2 * G_PI);
            cairo_stroke(cr);
        }
        break;
    }

    case SPICY_ICON_INPUT: {
        double x = 2.8, y = 6, w = 18.4, h = 12.5, r = 2.4;
        int i;
        cairo_new_sub_path(cr);
        cairo_arc(cr, x + w - r, y + r, r, -G_PI / 2, 0);
        cairo_arc(cr, x + w - r, y + h - r, r, 0, G_PI / 2);
        cairo_arc(cr, x + r, y + h - r, r, G_PI / 2, G_PI);
        cairo_arc(cr, x + r, y + r, r, G_PI, 3 * G_PI / 2);
        cairo_close_path(cr);
        cairo_stroke(cr);
        for (i = 0; i < 4; i++) {
            cairo_move_to(cr, 7 + i * 3.4, 9.6);
            cairo_line_to(cr, 7 + i * 3.4, 9.7);
        }
        for (i = 0; i < 3; i++) {
            cairo_move_to(cr, 8.7 + i * 3.4, 12.4);
            cairo_line_to(cr, 8.7 + i * 3.4, 12.5);
        }
        cairo_stroke(cr);
        cairo_move_to(cr, 8.5, 15.3);
        cairo_line_to(cr, 15.5, 15.3);
        cairo_stroke(cr);
        break;
    }

    case SPICY_ICON_CHEVRON:
        cairo_move_to(cr, 7.5, 10);
        cairo_line_to(cr, 12, 14.5);
        cairo_line_to(cr, 16.5, 10);
        cairo_stroke(cr);
        break;
    }
}

static gboolean spicy_theme_icon_draw(GtkWidget *widget, cairo_t *cr, gpointer data)
{
    GtkStyleContext *style = gtk_widget_get_style_context(widget);
    gint icon = GPOINTER_TO_INT(data);
    gint width = gtk_widget_get_allocated_width(widget);
    gint height = gtk_widget_get_allocated_height(widget);
    gint size = MIN(width, height);
    GdkRGBA color;

    gtk_style_context_get_color(style, gtk_style_context_get_state(style), &color);

    cairo_save(cr);
    cairo_translate(cr, (width - size) / 2.0, (height - size) / 2.0);
    cairo_scale(cr, size / 24.0, size / 24.0);
    cairo_set_line_width(cr, icon == SPICY_ICON_CHEVRON ? 2.0 : 1.9);
    cairo_set_line_cap(cr, CAIRO_LINE_CAP_ROUND);
    cairo_set_line_join(cr, CAIRO_LINE_JOIN_ROUND);
    gdk_cairo_set_source_rgba(cr, &color);
    spicy_theme_icon_path(cr, icon);
    cairo_restore(cr);

    return FALSE;
}

static GtkWidget *spicy_theme_icon_new(gint icon, gint size)
{
    GtkWidget *area = gtk_drawing_area_new();
    gtk_widget_set_size_request(area, size, size);
    gtk_widget_set_valign(area, GTK_ALIGN_CENTER);
    g_signal_connect(area, "draw", G_CALLBACK(spicy_theme_icon_draw), GINT_TO_POINTER(icon));
    return area;
}

GtkWidget *spicy_theme_button_new(SpicyIcon icon, const gchar *label, gboolean danger,
                                  gboolean opens_menu)
{
    GtkWidget *button = gtk_button_new();
    GtkWidget *box = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 8);
    GtkWidget *text = gtk_label_new(label);

    gtk_box_pack_start(GTK_BOX(box), spicy_theme_icon_new(icon, SPICY_ICON_SIZE), FALSE, FALSE, 0);
    gtk_box_pack_start(GTK_BOX(box), text, FALSE, FALSE, 0);

    if (opens_menu) {
        GtkWidget *chevron = spicy_theme_icon_new(SPICY_ICON_CHEVRON, 14);
        gtk_style_context_add_class(gtk_widget_get_style_context(chevron), "gvs-chevron");
        gtk_widget_set_margin_start(chevron, -2);
        gtk_widget_set_margin_end(chevron, -4);
        gtk_box_pack_start(GTK_BOX(box), chevron, FALSE, FALSE, 0);
    }

    gtk_container_add(GTK_CONTAINER(button), box);
    gtk_widget_set_can_focus(button, FALSE);
    gtk_button_set_relief(GTK_BUTTON(button), GTK_RELIEF_NONE);
    if (danger)
        gtk_style_context_add_class(gtk_widget_get_style_context(button), "gvs-danger");
    gtk_widget_show_all(button);

    return button;
}

GtkWidget *spicy_theme_separator_new(void)
{
    GtkWidget *separator = gtk_separator_new(GTK_ORIENTATION_VERTICAL);
    gtk_style_context_add_class(gtk_widget_get_style_context(separator), "gvs-separator");
    return separator;
}

GtkWidget *spicy_theme_grip_new(void)
{
    GtkWidget *grip = gtk_box_new(GTK_ORIENTATION_HORIZONTAL, 0);
    gtk_style_context_add_class(gtk_widget_get_style_context(grip), "gvs-grip");
    gtk_widget_set_halign(grip, GTK_ALIGN_CENTER);
    gtk_widget_set_valign(grip, GTK_ALIGN_START);
    return grip;
}

static void spicy_theme_style_one_menu(GtkWidget *menu, gboolean styled)
{
    GtkWidget *toplevel = gtk_widget_get_toplevel(menu);
    GList *items, *l;

    if (styled) {
        gtk_style_context_add_class(gtk_widget_get_style_context(menu), "gvs-menu");
        if (gdk_screen_is_composited(gtk_widget_get_screen(menu)))
            gtk_style_context_add_class(gtk_widget_get_style_context(menu), "gvs-rounded");
        else
            gtk_style_context_remove_class(gtk_widget_get_style_context(menu), "gvs-rounded");
        if (toplevel && toplevel != menu && GTK_IS_WINDOW(toplevel))
            gtk_style_context_add_class(gtk_widget_get_style_context(toplevel), "gvs-menu-window");
    } else {
        gtk_style_context_remove_class(gtk_widget_get_style_context(menu), "gvs-menu");
        gtk_style_context_remove_class(gtk_widget_get_style_context(menu), "gvs-rounded");
        if (toplevel && toplevel != menu && GTK_IS_WINDOW(toplevel))
            gtk_style_context_remove_class(gtk_widget_get_style_context(toplevel), "gvs-menu-window");
    }

    items = gtk_container_get_children(GTK_CONTAINER(menu));
    for (l = items; l != NULL; l = l->next) {
        if (GTK_IS_MENU_ITEM(l->data)) {
            GtkWidget *submenu = gtk_menu_item_get_submenu(GTK_MENU_ITEM(l->data));
            if (submenu && GTK_IS_MENU(submenu))
                spicy_theme_style_one_menu(submenu, styled);
        }
    }
    g_list_free(items);
}

void spicy_theme_style_menu(GtkWidget *menu, gboolean styled)
{
    g_return_if_fail(GTK_IS_MENU(menu));
    spicy_theme_style_one_menu(menu, styled);
}
