// APMine companion change, 2026-10-13: separate keys, markers and weapon icons.
//
// Copyright(C) 2023 David St-Louis
//
// This program is free software; you can redistribute it and/or
// modify it under the terms of the GNU General Public License
// as published by the Free Software Foundation; either version 2
// of the License, or (at your option) any later version.
//
// This program is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
//
// *Level select feature for archipelago*
//

#include "apdoom.h"
#include "ap_lsel.h"
#include "level_select.h"
#include "level_select_layout.h"

#include "crispy.h"
#include "doomdef.h"
#include "doomkeys.h"
#include "doomstat.h"
#include "i_system.h"
#include "i_video.h"
#include "d_main.h"
#include "d_player.h"
#include "g_game.h"
#include "hu_lib.h"
#include "i_swap.h"
#include "m_misc.h"
#include "m_controls.h"
#include "hu_stuff.h"
#include "s_sound.h"
#include "v_video.h"
#include "v_trans.h"
#include "w_wad.h"
#include "z_zone.h"

#include <string.h>


void WI_initAnimatedBack(void);
void WI_updateAnimatedBack(void);
void WI_drawAnimatedBack(void);
void WI_initVariables(wbstartstruct_t* wbstartstruct);
void WI_loadData(void);

void G_DoSaveGame(void);
void G_MarkNextLoadAsLevelResume(int episode, int map);
void set_ap_player_states(void);

// Functions in "st_stuff.c" needed for drawing status bar graphics
void ST_DrawKey(int x, int y, int which, boolean is_skull);

static wbstartstruct_t wiinfo;

extern int bcnt;

int ep_anim = 0;
int initial_delay = 0;
int urh_anim = 0;

enum
{
    LEVEL_SELECT_WHITE_COLOR = 80,
    LEVEL_SELECT_TEXT_COLOR = 160,
    LEVEL_SELECT_COMPLETE_COLOR = 112,
    LEVEL_SELECT_LOCKED_COLOR = 176,
};

typedef enum
{
    LEVEL_SELECT_STATUS_DEFAULT,
    LEVEL_SELECT_STATUS_WHITE,
    LEVEL_SELECT_STATUS_COMPLETE,
} level_select_status_t;

typedef struct
{
    char c;
    byte width;
    byte rows[5];
} level_select_font_glyph_t;

static const level_select_font_glyph_t level_select_font[] =
{
    {'0', 3, {7, 5, 5, 5, 7}},
    {'1', 3, {2, 6, 2, 2, 7}},
    {'2', 3, {7, 1, 7, 4, 7}},
    {'3', 3, {7, 1, 7, 1, 7}},
    {'4', 3, {5, 5, 7, 1, 1}},
    {'5', 3, {7, 4, 7, 1, 7}},
    {'6', 3, {7, 4, 7, 5, 7}},
    {'7', 3, {7, 1, 1, 2, 2}},
    {'8', 3, {7, 5, 7, 5, 7}},
    {'9', 3, {7, 5, 7, 1, 7}},
    {'A', 3, {2, 5, 7, 5, 5}},
    {'E', 3, {7, 4, 6, 4, 7}},
    {'M', 5, {17, 27, 21, 17, 17}},
    {'P', 3, {6, 5, 6, 4, 4}},
    {'W', 5, {17, 17, 21, 21, 10}},
    {':', 1, {0, 1, 0, 1, 0}},
    {'/', 3, {1, 1, 2, 4, 4}},
};

static const level_select_font_glyph_t *LevelSelectFindGlyph(char c)
{
    if (c >= 'a' && c <= 'z')
        c -= 'a' - 'A';

    for (int i = 0; i < arrlen(level_select_font); ++i)
    {
        if (level_select_font[i].c == c)
            return &level_select_font[i];
    }

    return NULL;
}

int LevelSelectTextWidth(const char *text)
{
    int width = 0;

    for (int i = 0; text[i] != '\0'; ++i)
    {
        const level_select_font_glyph_t *glyph = LevelSelectFindGlyph(text[i]);

        width += glyph ? glyph->width + 1 : 3;
    }

    return width;
}

static void DrawLevelSelectPixelBox(int x, int y, int w, int h, int color)
{
    if (crispy->hires)
    {
        V_DrawFilledBox(
            (x + WIDESCREENDELTA) << crispy->hires,
            y << crispy->hires,
            w << crispy->hires,
            h << crispy->hires,
            color);
        return;
    }

    V_DrawFilledBox(x, y, w, h, color);
}

void DrawLevelSelectTextColor(int x, int y, const char *text, int color)
{
    for (int i = 0; text[i] != '\0'; ++i)
    {
        const level_select_font_glyph_t *glyph = LevelSelectFindGlyph(text[i]);

        if (!glyph)
        {
            x += 3;
            continue;
        }

        for (int row = 0; row < 5; ++row)
        {
            for (int col = 0; col < glyph->width; ++col)
            {
                if (glyph->rows[row] & (1 << (glyph->width - col - 1)))
                    DrawLevelSelectPixelBox(x + col, y + row, 1, 1, color);
            }
        }

        x += glyph->width + 1;
    }
}

void DrawLevelSelectText(int x, int y, const char *text)
{
    DrawLevelSelectTextColor(x + 1, y + 1, text, 0);
    DrawLevelSelectTextColor(x, y, text, LEVEL_SELECT_TEXT_COLOR);
}

static int LevelSelectStatusColor(level_select_status_t status)
{
    switch (status)
    {
        case LEVEL_SELECT_STATUS_COMPLETE:
            return LEVEL_SELECT_COMPLETE_COLOR;
        case LEVEL_SELECT_STATUS_WHITE:
            return LEVEL_SELECT_WHITE_COLOR;
        default:
            return LEVEL_SELECT_TEXT_COLOR;
    }
}

static void DrawLevelSelectTextStatus(int x, int y, const char *text, level_select_status_t status)
{
    DrawLevelSelectTextColor(x + 1, y + 1, text, 0);
    DrawLevelSelectTextColor(x, y, text, LevelSelectStatusColor(status));
}

static level_select_status_t LevelSelectCountStatus(int current, int total, boolean complete, int white_minimum)
{
    if (complete)
        return LEVEL_SELECT_STATUS_COMPLETE;

    if (current >= white_minimum)
        return LEVEL_SELECT_STATUS_WHITE;

    return LEVEL_SELECT_STATUS_DEFAULT;
}

static boolean LevelSelectMapKeyObtained(const ap_level_state_t *level_state, int key)
{
    if (ap_state.vanilla_keys_mode)
        return true;

    return level_state && level_state->keys[key];
}

static boolean LevelSelectAllMapKeysObtained(const ap_level_info_t *level_info, const ap_level_state_t *level_state)
{
    boolean has_keys = false;

    for (int i = 0; i < 3; ++i)
    {
        if (!level_info->keys[i])
            continue;

        has_keys = true;
        if (!LevelSelectMapKeyObtained(level_state, i))
            return false;
    }

    return has_keys;
}

static void LevelSelectNormalizeMapKeys(ap_level_info_t *level_info)
{
    if (!level_info
        || gamemode != commercial
        || level_info->game_episode != 1
        || level_info->game_map != 25
        || !level_info->name
        || strcmp(level_info->name, "Bloodfalls (MAP25)") != 0)
    {
        return;
    }

    level_info->keys[0] = 1;
    level_info->keys[1] = 0;
    level_info->keys[2] = 0;
    level_info->use_skull[0] = 1;
    level_info->use_skull[1] = 0;
    level_info->use_skull[2] = 0;
}

static void DrawLevelSelectMapLabel(int x, int y, const char *text, boolean completed, boolean locked, boolean all_keys_obtained)
{
    const int color = locked
        ? LEVEL_SELECT_LOCKED_COLOR
        : completed ? LEVEL_SELECT_COMPLETE_COLOR
        : all_keys_obtained ? LEVEL_SELECT_WHITE_COLOR : LEVEL_SELECT_TEXT_COLOR;

    DrawLevelSelectTextColor(x + 1, y + 1, text, 0);
    DrawLevelSelectTextColor(x, y, text, color);
}

static void DrawLevelSelectPatchStatus(int x, int y, patch_t *patch, boolean complete, boolean locked, boolean all_keys_obtained)
{
    if (complete)
        dp_translation = cr[CR_GREEN];
    else if (locked)
        dp_translation = cr[CR_DARK];
    else if (all_keys_obtained)
        dp_translation = cr[CR_GRAY];

    V_DrawPatch(x, y, patch);

    if (complete || locked || all_keys_obtained)
        dp_translation = NULL;
}


static void DrawLevelSelectPatchMaybeComplete(int x, int y, patch_t *patch, boolean complete)
{
    DrawLevelSelectPatchStatus(x, y, patch, complete, false, false);
}

static void DrawSelectedLevelName(
    const ap_levelselect_t *screen_defs,
    const ap_levelselect_map_t *mapinfo,
    boolean completed,
    int episode,
    int map)
{
    char doom1_graphic[9];
    const char *graphic = mapinfo->map_name.graphic;

    if (!graphic[0] && gamemode != commercial)
    {
        M_snprintf(doom1_graphic, sizeof(doom1_graphic), "WILV%d%d", episode, map);
        if (W_CheckNumForName(doom1_graphic) >= 0)
            graphic = doom1_graphic;
    }

    if (graphic[0] && W_CheckNumForName(graphic) >= 0)
    {
        patch_t *patch = W_CacheLumpName(graphic, PU_CACHE);
        const int x = (ORIGWIDTH - patch->width) / 2;
        const int y = (screen_defs->map_names < 0 || gamemode == commercial) ? 2 : (ORIGHEIGHT - patch->height) - 2;

        DrawLevelSelectPatchMaybeComplete(x, y, patch, completed);
    }
    else if (mapinfo->map_name.text)
    {
        const int width = HULib_measureText(mapinfo->map_name.text, strlen(mapinfo->map_name.text));
        const int x = (ORIGWIDTH - width) / 2;
        const int y = (screen_defs->map_names < 0 || gamemode == commercial) ? 2 : ORIGHEIGHT - 12;

        HUlib_drawText(mapinfo->map_name.text, x, y);
    }
}

static void DrawLevelSelectCountLine(
    const char *label,
    int x,
    int y,
    int current,
    int total,
    boolean complete,
    int white_minimum)
{
    const int label_width = LevelSelectTextWidth(label);
    char count_text[16];
    const level_select_status_t status = LevelSelectCountStatus(current, total, complete, white_minimum);

    DrawLevelSelectTextStatus(x, y, label, status);

    M_snprintf(count_text, sizeof(count_text), " %d/%d", current, total);
    DrawLevelSelectTextStatus(x + label_width, y, count_text, status);
}

static void DrawLevelSelectCountLineRight(
    const char *label,
    int right_x,
    int y,
    int current,
    int total,
    boolean complete,
    int white_minimum)
{
    char text[24];
    const level_select_status_t status = LevelSelectCountStatus(current, total, complete, white_minimum);

    M_snprintf(text, sizeof(text), "%s %d/%d", label, current, total);
    DrawLevelSelectTextStatus(right_x - LevelSelectTextWidth(text), y, text, status);
}

static void DrawLevelSelectDoom2ProgressLine(
    int x,
    int y,
    int map_num,
    boolean completed,
    boolean locked,
    int checks_current,
    int checks_total,
    int weapons_current,
    int weapons_total,
    boolean checks_complete,
    boolean weapons_complete,
    boolean all_keys_obtained)
{
    patch_t *lock_marker = W_CacheLumpName("WILOCK", PU_CACHE);
    patch_t *splat_marker = W_CacheLumpName("WISPLAT", PU_CACHE);
    const int lock_right = x - SHORT(lock_marker->leftoffset) + SHORT(lock_marker->width);
    const int splat_right = x - SHORT(splat_marker->leftoffset) + SHORT(splat_marker->width);
    const int lock_top = y - SHORT(lock_marker->topoffset);
    const int splat_top = y - SHORT(splat_marker->topoffset);
    const int lock_bottom = lock_top + SHORT(lock_marker->height);
    const int splat_bottom = splat_top + SHORT(splat_marker->height);
    const int marker_right = (lock_right > splat_right ? lock_right : splat_right);
    const int marker_top = (lock_top < splat_top ? lock_top : splat_top);
    const int marker_bottom = (lock_bottom > splat_bottom ? lock_bottom : splat_bottom);
    const int text_x = marker_right + WIDESCREENDELTA + 4;
    const int text_y = marker_top + (marker_bottom - marker_top - 5) / 2;
    char map_text[8];
    char checks_text[16];
    char weapons_text[16];
    int draw_x = text_x;

    M_snprintf(map_text, sizeof(map_text), "MAP%02d ", map_num);
    M_snprintf(checks_text, sizeof(checks_text), "AP: %d/%d ", checks_current, checks_total);
    M_snprintf(weapons_text, sizeof(weapons_text), "W: %d/%d", weapons_current, weapons_total);

    DrawLevelSelectMapLabel(draw_x, text_y, map_text, completed, locked, all_keys_obtained);
    draw_x += LevelSelectTextWidth(map_text);

    DrawLevelSelectTextStatus(draw_x, text_y, checks_text, LevelSelectCountStatus(checks_current, checks_total, checks_complete, 1));
    draw_x += LevelSelectTextWidth(checks_text);

    DrawLevelSelectTextStatus(draw_x, text_y, weapons_text, LevelSelectCountStatus(weapons_current, weapons_total, weapons_complete, 1));
}

static int LevelSelectDoom2ProgressLineRight(
    int x,
    int y,
    int map_num,
    int checks_current,
    int checks_total,
    int weapons_current,
    int weapons_total)
{
    patch_t *lock_marker = W_CacheLumpName("WILOCK", PU_CACHE);
    patch_t *splat_marker = W_CacheLumpName("WISPLAT", PU_CACHE);
    const int lock_right = x - SHORT(lock_marker->leftoffset) + SHORT(lock_marker->width);
    const int splat_right = x - SHORT(splat_marker->leftoffset) + SHORT(splat_marker->width);
    const int marker_right = (lock_right > splat_right ? lock_right : splat_right);
    char map_text[8];
    char checks_text[16];
    char weapons_text[16];

    M_snprintf(map_text, sizeof(map_text), "MAP%02d ", map_num);
    M_snprintf(checks_text, sizeof(checks_text), "AP: %d/%d ", checks_current, checks_total);
    M_snprintf(weapons_text, sizeof(weapons_text), "W: %d/%d", weapons_current, weapons_total);

    return marker_right + WIDESCREENDELTA + 4
        + LevelSelectTextWidth(map_text)
        + LevelSelectTextWidth(checks_text)
        + LevelSelectTextWidth(weapons_text);
}

static int LevelSelectDoom2KeyAnchorX(const ap_levelselect_t *screen_defs, int map_count)
{
    int right_x = 0;

    for (int i = 0; i < map_count; ++i)
    {
        ap_level_index_t idx = {selected_ep, i};
        ap_level_info_t *ap_level_info = ap_get_level_info(idx);
        ap_level_state_t *ap_level_state = ap_get_level_state(idx);
        const ap_levelselect_map_t *mapinfo = &screen_defs->map_info[i];
        const int line_right = LevelSelectDoom2ProgressLineRight(
            mapinfo->x,
            mapinfo->y,
            ap_level_info->game_map,
            ap_level_state->check_count,
            ap_total_check_count(ap_level_info),
            ap_level_weapon_count(idx),
            ap_total_level_weapon_count(idx));

        if (line_right > right_x)
            right_x = line_right;
    }

    return right_x - WIDESCREENDELTA + 4;
}

static void AppendWeaponLabel(char *text, size_t text_size, const char *label)
{
    if (text[0] != '\0')
        M_StringConcat(text, " ", text_size);

    M_StringConcat(text, label, text_size);
}

static void BuildLevelSelectWeaponText(const ap_level_state_t *level_state, char *text, size_t text_size)
{
    const int *weapon_owned = ap_state.level_specific_weapons
        ? level_state->weapon_owned
        : ap_state.player_state.weapon_owned;

    text[0] = '\0';

    if (weapon_owned[7])
        AppendWeaponLabel(text, text_size, "1");

    AppendWeaponLabel(text, text_size, "2");

    if (weapon_owned[2])
        AppendWeaponLabel(text, text_size, "3");

    if (gamemode == commercial && weapon_owned[8])
        AppendWeaponLabel(text, text_size, "3*");

    if (weapon_owned[3])
        AppendWeaponLabel(text, text_size, "4");

    if (weapon_owned[4])
        AppendWeaponLabel(text, text_size, "5");

    if (weapon_owned[5])
        AppendWeaponLabel(text, text_size, "6");

    if (weapon_owned[6])
        AppendWeaponLabel(text, text_size, "7");
}


void restart_wi_anims()
{
    wiinfo.epsd = selected_ep;
    WI_initVariables(&wiinfo);
    WI_loadData();
    WI_initAnimatedBack();
}


static boolean should_resume_pistol_start_level(ap_level_index_t idx)
{
    return ap_state.pistol_start
        && ap_state.resume_pistol_start_levels
        && !ap_get_level_state(idx)->completed;
}


void play_level(int ep, int lvl)
{
    ap_level_index_t idx = { ep, lvl };
    ep = ap_index_to_ep(idx);
    lvl = ap_index_to_map(idx);

    // Check if level has a save file first
    char filename[260];
    if (gamemode != commercial)
        snprintf(filename, 260, "%s/save_E%iM%i.dsg", apdoom_get_save_dir(), ep, lvl);
    else
        snprintf(filename, 260, "%s/save_MAP%02i.dsg", apdoom_get_save_dir(), lvl);

    if (ap_state.pistol_start && ap_state.resume_pistol_start_levels
        && ap_get_level_state(idx)->completed && M_FileExists(filename))
    {
        M_remove(filename);
    }

    if ((!ap_state.pistol_start || should_resume_pistol_start_level(idx)) && M_FileExists(filename))
    {
        // We load
        extern char savename[256];
        snprintf(savename, 256, "%s", filename);
        G_MarkNextLoadAsLevelResume(ep, lvl);
        gameaction = ga_loadgame;
        //G_DoLoadGame();
    }
    else
    {
        // If none, load it fresh
        G_DeferedInitNew(gameskill, ep, lvl);
    }
    HU_ClearAPMessages();
}


void select_map_dir(int dir)
{
    const ap_levelselect_t* screen_defs = LS_CurrentEpisodeInfo();

    int from = selected_level[selected_ep];
    float fromx = (float)screen_defs->map_info[from].x;
    float fromy = (float)screen_defs->map_info[from].y;

    int best = from;
    int top_most = 200;
    int top_most_idx = -1;
    int bottom_most = 0;
    int bottom_most_idx = -1;
    float best_score = 0.0f;

    int map_count = ap_get_map_count(selected_ep + 1);
    for (int i = 0; i < map_count; ++i)
    {
        if (screen_defs->map_info[i].y < top_most)
        {
            top_most = screen_defs->map_info[i].y;
            top_most_idx = i;
        }
        if (screen_defs->map_info[i].y > bottom_most)
        {
            bottom_most = screen_defs->map_info[i].y;
            bottom_most_idx = i;
        }
        if (i == from) continue;

        float tox = (float)screen_defs->map_info[i].x;
        float toy = (float)screen_defs->map_info[i].y;
        float score = 0.0f;
        float dist = 10000.0f;

        switch (dir)
        {
            case 0: // Left
                if (tox >= fromx) continue;
                dist = fromx - tox;
                break;
            case 1: // Right
                if (tox <= fromx) continue;
                dist = tox - fromx;
                break;
            case 2: // Up
                if (toy >= fromy) continue;
                dist = fromy - toy;
                break;
            case 3: // Down
                if (toy <= fromy) continue;
                dist = toy - fromy;
                break;
        }
        score = 1.0f / dist;

        if (score > best_score)
        {
            best_score = score;
            best = i;
        }
    }

    // Are we at the top? go to the bottom
    if (from == top_most_idx && dir == 2)
    {
        best = bottom_most_idx;
    }
    else if (from == bottom_most_idx && dir == 3)
    {
        best = top_most_idx;
    }

    if (best != from)
    {
        urh_anim = 0;
        S_StartSoundOptional(NULL, sfx_mnusli, sfx_stnmov);
        selected_level[selected_ep] = best;
    }
}


static void level_select_nav_left()
{
    select_map_dir(0);
}


static void level_select_nav_right()
{
    select_map_dir(1);
}


static void level_select_nav_up()
{
    select_map_dir(2);
}


static void level_select_nav_down()
{
    select_map_dir(3);
}


static void level_select_prev_episode()
{
    int new_ep = LS_PrevEpisode();
    if (new_ep == selected_ep)
        return;

    selected_ep = new_ep;
    ep_anim = -10;
    urh_anim = 0;
    restart_wi_anims();
    S_StartSoundOptional(NULL, sfx_mnucls, sfx_swtchx);
}


static void level_select_next_episode()
{
    int new_ep = LS_NextEpisode();
    if (new_ep == selected_ep)
        return;

    selected_ep = new_ep;
    ep_anim = 10;
    urh_anim = 0;
    restart_wi_anims();
    S_StartSoundOptional(NULL, sfx_mnucls, sfx_swtchx);
}


static void level_select_nav_enter()
{
    ap_level_index_t idx = {selected_ep, selected_level[selected_ep]};
    if (ap_get_level_state(idx)->unlocked)
    {
        S_StartSoundOptional(NULL, sfx_mnusli, sfx_swtchn);
        play_level(selected_ep, selected_level[selected_ep]);
    }
    else
    {
        S_StartSound(NULL, sfx_noway);
    }
}


boolean LevelSelectResponder(event_t* ev)
{
    if (ep_anim || initial_delay) return true;

    switch (ev->type)
    {
        case ev_joystick:
        {
            if (ev->data4 < 0 || ev->data2 < 0)
            {
                level_select_nav_left();
                joywait = I_GetTime() + 5;
            }
            else if (ev->data4 > 0 || ev->data2 > 0)
            {
                level_select_nav_right();
                joywait = I_GetTime() + 5;
            }
            else if (ev->data3 < 0)
            {
                level_select_nav_up();
                joywait = I_GetTime() + 5;
            }
            else if (ev->data3 > 0)
            {
                level_select_nav_down();
                joywait = I_GetTime() + 5;
            }

#define JOY_BUTTON_MAPPED(x) ((x) >= 0)
#define JOY_BUTTON_PRESSED(x) (JOY_BUTTON_MAPPED(x) && (ev->data1 & (1 << (x))) != 0)

            if (JOY_BUTTON_PRESSED(joybfire)) level_select_nav_enter();

            if (JOY_BUTTON_PRESSED(joybprevweapon)) level_select_prev_episode();
            else if (JOY_BUTTON_PRESSED(joybnextweapon)) level_select_next_episode();

            break;
        }
        case ev_keydown:
        {
            if (ev->data1 == key_left || ev->data1 == key_alt_strafeleft || ev->data1 == key_strafeleft) level_select_prev_episode();
            if (ev->data1 == key_right || ev->data1 == key_alt_straferight || ev->data1 == key_straferight) level_select_next_episode();
            if (ev->data1 == key_up || ev->data1 == key_alt_up) level_select_nav_up();
            if (ev->data1 == key_down || ev->data1 == key_alt_down) level_select_nav_down();
            if (ev->data1 == key_menu_forward || ev->data1 == key_use) level_select_nav_enter();
            break;
        }
        default:
            break;
    }

    return true;
}


void ShowLevelSelect()
{
    LS_Start();
    HU_ClearAPMessages();

    // Level Select removes Berserk power from the player.
    ap_state.player_state.powers[pw_strength] = 0;
    players[consoleplayer].powers[pw_strength] = 0;

    // If in a level, save current level
    if (gamestate == GS_LEVEL)
    {
        ap_level_index_t idx = ap_make_level_index(gameepisode, gamemap);
        if (!ap_state.pistol_start || should_resume_pistol_start_level(idx))
            G_DoSaveGame();
    }

    if (crispy->ap_levelselectmusic)
    {
        if (gamemode == commercial)
            S_ChangeMusic(mus_read_m, true);
        else
            S_ChangeMusic(mus_inter, true);
    }
    else
        S_StopMusic();

    gameaction = ga_nothing;
    gamestate = GS_LEVEL_SELECT;
    viewactive = false;
    automapactive = false;

    memset(&wiinfo, 0, sizeof(wbstartstruct_t));
    wiinfo.epsd = selected_ep;
    wiinfo.didsecret = false;
    wiinfo.last = -1;
    wiinfo.next = -1;
    
    restart_wi_anims();
    bcnt = 0;
    ep_anim = 0;
    initial_delay = 2; // Just a couple frames of no input to prevent accidental movement

    // Necessary to ensure player info is correct for EnergyLink, etc
    set_ap_player_states();
}


void TickLevelSelect()
{
    if (initial_delay)    --initial_delay;
    if (ep_anim > 0)      --ep_anim;
    else if (ep_anim < 0) ++ep_anim;
    bcnt++;
    urh_anim = (urh_anim + 1) % 35;
    WI_updateAnimatedBack();
}


static int LevelSelectCenteredPatchX(patch_t* patch, int center_x)
{
    return center_x - (SHORT(patch->width) / 2) + SHORT(patch->leftoffset);
}


static int LevelSelectCenteredPatchY(patch_t* patch, int center_y)
{
    return center_y - (SHORT(patch->height) / 2) + SHORT(patch->topoffset);
}


static int LevelSelectScaledPatchWidth(patch_t* patch, int scale_num, int scale_den)
{
    return (SHORT(patch->width) * scale_num + scale_den - 1) / scale_den;
}


static int LevelSelectScaledPatchHeight(patch_t* patch, int scale_num, int scale_den)
{
    return (SHORT(patch->height) * scale_num + scale_den - 1) / scale_den;
}


static int LevelSelectCenteredScaledPatchX(patch_t* patch, int center_x, int scale_num, int scale_den)
{
    return center_x
        - (LevelSelectScaledPatchWidth(patch, scale_num, scale_den) / 2)
        + ((SHORT(patch->leftoffset) * scale_num) / scale_den);
}


static void DrawSelectedLevelWeaponDisplay(void)
{
    enum
    {
        MAX_DISPLAY_WEAPONS = 8
    };

    ap_level_weapon_display_t weapons[MAX_DISPLAY_WEAPONS];
    ap_level_index_t idx = {selected_ep, selected_level[selected_ep]};
    const int count = ap_get_level_weapon_display(idx, weapons, MAX_DISPLAY_WEAPONS);

    if (count <= 0)
        return;

    for (int i = 0; i < count; ++i)
    {
        patch_t* weapon_patch = W_CacheLumpName(weapons[i].sprite, PU_CACHE);
        const int slot_x = (ORIGWIDTH / 2) + (i * APMINE_WEAPON_SPACING) - (((count - 1) * APMINE_WEAPON_SPACING) / 2);
        int scale_num, scale_den;
        APMineLevelSelectWeaponScale(SHORT(weapon_patch->width), SHORT(weapon_patch->height), &scale_num, &scale_den);
        const int weapon_height = LevelSelectScaledPatchHeight(weapon_patch, scale_num, scale_den);
        int weapon_y;

        weapon_y = APMINE_WEAPON_BOTTOM - weapon_height;

        if (weapons[i].logic && W_CheckNumForName("CHECKMRK") >= 0)
        {
            V_DrawPatch(slot_x + 15, APMINE_WEAPON_BOTTOM - 7, W_CacheLumpName("CHECKMRK", PU_CACHE));
        }

        if (!weapons[i].owned)
            dp_translation = cr_levelselect_keydark;

        V_DrawPatchScaled(
            LevelSelectCenteredScaledPatchX(weapon_patch, slot_x, scale_num, scale_den),
            weapon_y + ((SHORT(weapon_patch->topoffset) * scale_num) / scale_den),
            weapon_patch,
            scale_num,
            scale_den);

        dp_translation = NULL;
    }
}


void DrawEpisodicLevelSelectStats()
{
    const ap_levelselect_t* screen_defs = LS_CurrentEpisodeInfo();
    const int map_count = ap_get_map_count(selected_ep + 1);
    const int doom2_key_anchor_x = (gamemode == commercial)
        ? LevelSelectDoom2KeyAnchorX(screen_defs, map_count)
        : 0;

    for (int i = 0; i < map_count; ++i)
    {
        ap_level_index_t idx = {selected_ep, i};
        ap_level_info_t* ap_level_info = ap_get_level_info(idx);
        ap_level_state_t* ap_level_state = ap_get_level_state(idx);
        LevelSelectNormalizeMapKeys(ap_level_info);
        const int checks_current = ap_level_state->check_count;
        const int checks_total = ap_total_check_count(ap_level_info);
        const int weapons_current = ap_level_weapon_count(idx);
        const int weapons_total = ap_total_level_weapon_count(idx);
        const boolean checks_complete = checks_total > 0 && checks_current >= checks_total;
        const boolean weapons_complete = weapons_total > 0 && weapons_current >= weapons_total;
        const boolean all_keys_obtained = LevelSelectAllMapKeysObtained(ap_level_info, ap_level_state);

        const ap_levelselect_map_t* mapinfo = &screen_defs->map_info[i];
        const int x = mapinfo->x;
        const int y = gamemode == commercial
            ? APMineLevelSelectListY(mapinfo->y, screen_defs->map_info[0].y, screen_defs->map_info[map_count - 1].y)
            : mapinfo->y;
        const int marker_y = gamemode == commercial ? y : y - 12;

        int key_x, key_y;
        int map_name_width = 0;
        int key_count = 0;

        for (int i = 0; i < 3; ++i)
            if (ap_level_info->keys[i])
                key_count++;

        // Level name display ("Individual" mode)
        if (gamemode != commercial && screen_defs->map_names == 0 && mapinfo->map_name.graphic[0])
        {
            patch_t* patch = W_CacheLumpName(mapinfo->map_name.graphic, PU_CACHE);
            DrawLevelSelectPatchStatus(
                x + mapinfo->map_name.x,
                y + mapinfo->map_name.y,
                patch,
                ap_level_state->completed,
                !ap_level_state->unlocked,
                all_keys_obtained);
            map_name_width = patch->width; // Store for later
        }
        
        // Level complete splash
        if (ap_level_state->completed)
            V_DrawPatch(x, marker_y, W_CacheLumpName("WISPLAT", PU_CACHE));

        // Available but incomplete marker
        if (gamemode != commercial
            && ap_level_state->unlocked
            && !ap_level_state->completed
            && W_CheckNumForName("WIAVAIL") >= 0)
        {
            V_DrawPatch(x, marker_y, W_CacheLumpName("WIAVAIL", PU_CACHE));
        }

        // Lock
        if (!ap_level_state->unlocked)
            V_DrawPatch(x, marker_y, W_CacheLumpName("WILOCK", PU_CACHE));

        // Keys
        {
            key_x = x + mapinfo->keys.x + (mapinfo->keys.align_x * key_count);
            key_y = y + mapinfo->keys.y + (mapinfo->keys.align_y * key_count);
            switch (mapinfo->keys.relative_to)
            {
                default:
                    break;
                case 2:
                    key_x += map_name_width;
                    // fall through
                case 1:
                    key_x += mapinfo->map_name.x;
                    key_y += mapinfo->map_name.y;
                    break;
            }

            if (gamemode != commercial)
            {
                key_x = x + APMINE_MAP_KEY_X;
                key_y = y - 13 + ((16 - (3 * 9)) / 2) + (2 * 9);
            }
            else
            {
                key_x = doom2_key_anchor_x;
            }

            for (int k = 0; k < 3; ++k)
            {
                const int spacing_x = (gamemode == commercial)
                    ? mapinfo->keys.spacing_x + (mapinfo->keys.spacing_x < 0 ? -3 : 3)
                    : 0;
                const int spacing_y = (gamemode == commercial) ? mapinfo->keys.spacing_y : -9;

                if (!ap_level_info->keys[k])
                    continue;

                patch_t *key_bg = W_CacheLumpName("KEYBG", PU_CACHE);
                const int draw_key_y = (gamemode == commercial)
                    ? key_y - SHORT(key_bg->height) + 4
                    : key_y;

                V_DrawPatch(key_x, draw_key_y, key_bg);
                if (!LevelSelectMapKeyObtained(ap_level_state, k))
                    dp_translation = cr_levelselect_keydark;
                else
                    dp_translation = cr_levelselect_keybright;

                ST_DrawKey(key_x, draw_key_y, k, ap_level_info->use_skull[k]);
                dp_translation = NULL;

                if (mapinfo->keys.use_checkmark)
                {
                    const int checkmark_x = key_x + mapinfo->keys.checkmark_x;
                    const int checkmark_y = draw_key_y + mapinfo->keys.checkmark_y;

                    if (LevelSelectMapKeyObtained(ap_level_state, k))
                        V_DrawPatch(checkmark_x, checkmark_y, W_CacheLumpName("CHECKMRK", PU_CACHE));
                }

                key_x += spacing_x;
                key_y += spacing_y;
            }            
        }

        // Progress
        {
            boolean drew_marker_progress = false;
            int progress_x = x + mapinfo->checks.x;
            int progress_y = y + mapinfo->checks.y;
            switch (mapinfo->checks.relative_to)
            {
                default:
                    break;
                case 2:
                    progress_x += map_name_width;
                    // fall through
                case 1:
                    progress_x += mapinfo->map_name.x;
                    progress_y += mapinfo->map_name.y;
                    break;
                case 3:
                    progress_x += mapinfo->keys.x;
                    progress_y += mapinfo->keys.y;
                    break;
                case 4:
                    progress_x = key_x + mapinfo->checks.x;
                    progress_y = key_y + mapinfo->checks.y;
                    break;
            }

            if (mapinfo->cursor.graphic[0]
                && strncmp(mapinfo->cursor.graphic, "WIURH", 5) == 0)
            {
                const int progress_right_x = x + 16;
                char map_label[8];

                M_snprintf(map_label, sizeof(map_label), "E%dM%d", selected_ep + 1, i + 1);

                // DOOM episode maps use the lock/splat marker as the stable anchor.
                DrawLevelSelectMapLabel(
                    progress_right_x - LevelSelectTextWidth(map_label),
                    y - 10,
                    map_label,
                    ap_level_state->completed,
                    !ap_level_state->unlocked,
                    all_keys_obtained);

                DrawLevelSelectCountLineRight(
                    "AP:",
                    progress_right_x,
                    y - 4,
                    checks_current,
                    checks_total,
                    checks_complete,
                    1);

                DrawLevelSelectCountLineRight(
                    "W:",
                    progress_right_x,
                    y + 2,
                    weapons_current,
                    weapons_total,
                    weapons_complete,
                    1);

                drew_marker_progress = true;
            }
            else if (gamemode == commercial)
            {
                DrawLevelSelectDoom2ProgressLine(
                    x,
                    y,
                    ap_level_info->game_map,
                    ap_level_state->completed,
                    !ap_level_state->unlocked,
                    checks_current,
                    checks_total,
                    weapons_current,
                    weapons_total,
                    checks_complete,
                    weapons_complete,
                    all_keys_obtained);

                drew_marker_progress = true;
            }

            if (!drew_marker_progress)
            {
                DrawLevelSelectCountLine(
                    "AP:",
                    progress_x - 38,
                    progress_y,
                    checks_current,
                    checks_total,
                    checks_complete,
                    1);

                DrawLevelSelectCountLine(
                    "W:",
                    progress_x - 50,
                    progress_y + 8,
                    weapons_current,
                    weapons_total,
                    weapons_complete,
                    1);
            }
        }

        // You are here
        if (i == selected_level[selected_ep] && urh_anim < 25)
        {
            if (strncmp(mapinfo->cursor.graphic, "WIURH", 5) == 0)
            {
                V_DrawPatch(x, y, W_CacheLumpName(mapinfo->cursor.graphic, PU_CACHE));
            }
            else
            {
                V_DrawPatch(x + mapinfo->cursor.x, y + mapinfo->cursor.y,
                            W_CacheLumpName(mapinfo->cursor.graphic, PU_CACHE));
            }
        }
    }

    // Level name (non-"Individual" modes)
    if (screen_defs->map_names != 0 || gamemode == commercial)
    {
        const int sel_idx = selected_level[selected_ep];
        ap_level_index_t idx = {selected_ep, sel_idx};
        ap_level_state_t* ap_level_state = ap_get_level_state(idx);
        const ap_levelselect_map_t* mapinfo = &screen_defs->map_info[sel_idx];

        DrawSelectedLevelName(
            screen_defs,
            mapinfo,
            ap_level_state->completed,
            selected_ep,
            sel_idx);
    }

    DrawSelectedLevelWeaponDisplay();
}


void DrawLevelSelectStats()
{
    DrawEpisodicLevelSelectStats();
}


void DrawLevelSelect()
{
    patch_t *primary_image = W_CacheLumpName(LS_CurrentEpisodeInfo()->background_image, PU_CACHE);

    // Just in case, always fill with black, then draw the current selected episode background
    V_DrawFilledBox(0, 0, SCREENWIDTH, SCREENHEIGHT, 0);
    V_DrawPatch(ep_anim * 32, 0, primary_image);

    if (ep_anim == 0)
    {
        // We may have room to draw the images for previous and next episodes...
        if (SCREENWIDTH != NONWIDEWIDTH)
        {
            patch_t *left_image = W_CacheLumpName(LS_PrevEpisodeInfo()->background_image, PU_CACHE);
            patch_t *right_image = W_CacheLumpName(LS_NextEpisodeInfo()->background_image, PU_CACHE);

            dp_translation = cr[CR_DARK];
            V_DrawPatch(-320, 0, left_image);
            V_DrawPatch(320, 0, right_image);
            dp_translation = NULL;
        }

        WI_drawAnimatedBack();
        DrawLevelSelectStats();
    }
    else if (ep_anim > 0)
    {
        patch_t *secondary_image = W_CacheLumpName(LS_PrevEpisodeInfo()->background_image, PU_CACHE);
        V_DrawPatch(-(10 - ep_anim) * 32, 0, secondary_image);
    }
    else // ep_anim < 0
    {
        patch_t *secondary_image = W_CacheLumpName(LS_NextEpisodeInfo()->background_image, PU_CACHE);
        V_DrawPatch((10 + ep_anim) * 32, 0, secondary_image);
    }
}
