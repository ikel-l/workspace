#pragma once

#include <gtk/gtk.h>

G_BEGIN_DECLS

typedef enum {
    SPICY_ICON_POWER,
    SPICY_ICON_EXIT_FULLSCREEN,
    SPICY_ICON_OPTIONS,
    SPICY_ICON_INPUT,
} SpicyIcon;

void spicy_theme_load_fonts(void);

void spicy_theme_init(void);

GtkWidget *spicy_theme_button_new(SpicyIcon icon, const gchar *label, gboolean danger,
                                  gboolean opens_menu);

GtkWidget *spicy_theme_separator_new(void);

GtkWidget *spicy_theme_grip_new(void);

void spicy_theme_style_menu(GtkWidget *menu, gboolean styled);

G_END_DECLS
