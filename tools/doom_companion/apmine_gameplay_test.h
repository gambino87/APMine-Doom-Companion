/* SPDX-License-Identifier: GPL-3.0-only */
/* APMine companion change, 2026-10-12: owned offline gameplay acceptance. */
#ifndef APMINE_GAMEPLAY_TEST_H
#define APMINE_GAMEPLAY_TEST_H

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "p_local.h"
#include "m_argv.h"
#include "m_misc.h"
#include "i_system.h"
#include "g_game.h"
#include "apdoom.h"

extern void play_level(int episode_index, int map_index);

static int APMineKeyCard(int doom_type)
{
    switch (doom_type)
    {
        case 5: return it_bluecard;
        case 6: return it_yellowcard;
        case 13: return it_redcard;
        case 40: return it_blueskull;
        case 39: return it_yellowskull;
        case 38: return it_redskull;
        default: return -1;
    }
}

static mobj_t *APMineFindPickup(ap_level_index_t level, boolean key)
{
    ap_level_info_t *info = ap_get_level_info(level);
    thinker_t *thinker;
    for (thinker = thinkercap.next; thinker != &thinkercap; thinker = thinker->next)
    {
        mobj_t *object;
        int card;
        if (thinker->function.acp1 != (actionf_p1)P_MobjThinker)
            continue;
        object = (mobj_t *)thinker;
        if (!(object->flags & MF_SPECIAL) || object->index < 0 || object->index >= info->thing_count)
            continue;
        card = APMineKeyCard(info->thing_infos[object->index].doom_type);
        if (key && card >= 0)
            return object;
        if (!key && card < 0 && (object->sprite == SPR_APPI || object->sprite == SPR_APJI)
                && !ap_is_location_checked(level, object->index))
            return object;
    }
    return NULL;
}

static void APMineTouchPickup(mobj_t *object, player_t *player)
{
    if (!object || !player->mo || !P_TeleportMove(player->mo, object->x, object->y))
        I_Error("APMINE_GAMEPLAY: cannot position private fixture at spawned pickup");
    player->mo->z = player->mo->floorz;
    P_TouchSpecialThing(object, player->mo);
}

static void APMineGameplayStep(void)
{
    static int stage, frames, pickup_index = -1, key_index = -1, key_card = -1;
    static int saved_health, saved_armor, saved_ammo[NUMAMMO], saved_weapons[NUMWEAPONS];
    static angle_t initial_angle, saved_angle;
    static int mode;
    static char save_path[260];
    int parameter = M_CheckParmWithArgs("-apmine-gameplay-test", 1);
    ap_level_index_t level;
    player_t *player = &players[consoleplayer];
    ap_level_state_t *state;
    int i;
    if (!parameter)
        return;
    if (++frames > 900)
        I_Error("APMINE_GAMEPLAY: finite offline test timed out at stage %d", stage);
    if (!stage)
    {
        char marker[64] = {0};
        FILE *file = M_fopen("APMine-private-gameplay.marker", "rb");
        size_t length = file ? fread(marker, 1, sizeof(marker) - 1, file) : 0;
        if (file) fclose(file);
        if (!ap_practice_mode || ap_force_disable_behaviors || M_CheckParm("-apserver") || !nomonsters
                || !getenv("SDL_VIDEODRIVER") || strcmp(getenv("SDL_VIDEODRIVER"), "dummy")
                || length != strlen("APMine-private-gameplay-v1\n")
                || strcmp(marker, "APMine-private-gameplay-v1\n"))
            I_Error("APMINE_GAMEPLAY: requires an owned dummy-SDL offline practice fixture");
        if (!strcmp(myargv[parameter + 1], "carry")) mode = 0;
        else if (!strcmp(myargv[parameter + 1], "pistol")) mode = 1;
        else if (!strcmp(myargv[parameter + 1], "resume")) mode = 2;
        else I_Error("APMINE_GAMEPLAY: unknown private test case");
        ap_state.pistol_start = mode != 0;
        ap_state.resume_pistol_start_levels = mode == 2;
        level = ap_make_level_index(1, 2);
        if (!ap_get_level_state(level)->unlocked)
            I_Error("APMINE_GAMEPLAY: private test map is not unlocked");
        printf("APMINE_GAMEPLAY: start mode=%d game=%s\n", mode, gamemode == commercial ? "doom2" : "doom");
        play_level(level.ep, level.map);
        stage = 1;
        return;
    }
    level = ap_make_level_index(1, 2);
    state = ap_get_level_state(level);
    if (stage == 1 && gamestate == GS_LEVEL && gameaction == ga_nothing && gamemap == 2 && leveltime >= 8)
    {
        mobj_t *pickup;
        if (!player->mo || player->mo->health <= 0)
            I_Error("APMINE_GAMEPLAY: map did not create a live player");
        V_ScreenShot("APGAME%02i.%s");
        initial_angle = player->mo->angle;
        pickup = APMineFindPickup(level, false);
        if (!pickup) I_Error("APMINE_GAMEPLAY: no spawned AP pickup on private map");
        pickup_index = pickup->index;
        APMineTouchPickup(pickup, player);
        stage = 2;
    }
    else if (stage == 2 && ap_is_location_checked(level, pickup_index))
    {
        mobj_t *key = APMineFindPickup(level, true);
        if (!key) I_Error("APMINE_GAMEPLAY: no spawned key on private map");
        key_index = key->index;
        key_card = APMineKeyCard(ap_get_level_info(level)->thing_infos[key_index].doom_type);
        printf("APMINE_GAMEPLAY: key touch index=%d type=%d sprite=%d card=%d\n",
               key_index, ap_get_level_info(level)->thing_infos[key_index].doom_type, key->sprite, key_card);
        APMineTouchPickup(key, player);
        printf("APMINE_GAMEPLAY: key after touch checked=%d held=%d received=%d\n",
               ap_is_location_checked(level, key_index), player->cards[key_card], state->keys[key_card % 3]);
        stage = 3;
    }
    else if (stage == 3 && player->cards[key_card] && state->keys[key_card % 3])
    {
        if (!ap_is_location_checked(level, key_index))
            I_Error("APMINE_GAMEPLAY: spawned AP key did not mark its location checked");
        saved_health = player->health > 13 ? player->health - 13 : 1;
        player->health = player->mo->health = saved_health;
        saved_armor = player->armorpoints;
        saved_angle = initial_angle + ANG90;
        player->mo->angle = saved_angle;
        for (i = 0; i < NUMAMMO; ++i) saved_ammo[i] = player->ammo[i];
        for (i = 0; i < NUMWEAPONS; ++i) saved_weapons[i] = player->weaponowned[i];
        snprintf(save_path, sizeof(save_path), gamemode == commercial ? "%s/save_MAP02.dsg" : "%s/save_E1M2.dsg", apdoom_get_save_dir());
        printf("APMINE_GAMEPLAY: pickup=%d key=%d card=%d checked health=%d\n", pickup_index, key_index, key_card, saved_health);
        G_LevelSelect();
        stage = 4;
    }
    else if (stage == 4 && gamestate == GS_LEVEL_SELECT && gameaction == ga_nothing)
    {
        if (M_FileExists(save_path) != (mode != 1))
            I_Error("APMINE_GAMEPLAY: native hub save policy did not match test case");
        player->mo->angle = saved_angle + ANG90;
        player->health = player->mo->health = 1;
        play_level(level.ep, level.map);
        stage = 5;
    }
    else if (stage == 5 && gamestate == GS_LEVEL && gameaction == ga_nothing && gamemap == 2)
    {
        if (!player->cards[key_card] || !ap_is_location_checked(level, pickup_index))
            I_Error("APMINE_GAMEPLAY: level return lost key/check state");
        if (mode == 1)
        {
            int expected = ap_state.player_state.max_health / 2;
            if (expected < 1) expected = 1;
            if (player->health != expected || player->mo->angle != initial_angle)
                I_Error("APMINE_GAMEPLAY: pistol-start return did not reset health/position");
        }
        else
        {
            if (player->health != saved_health || player->armorpoints != saved_armor || player->mo->angle != saved_angle)
                I_Error("APMINE_GAMEPLAY: native save did not restore player vitals/angle");
            for (i = 0; i < NUMAMMO; ++i)
                if (player->ammo[i] != saved_ammo[i]) I_Error("APMINE_GAMEPLAY: native save did not restore ammo");
            for (i = 0; i < NUMWEAPONS; ++i)
                if (player->weaponowned[i] != saved_weapons[i]) I_Error("APMINE_GAMEPLAY: native save did not restore weapons");
        }
        printf("APMINE_GAMEPLAY: native return policy passed mode=%d\n", mode);
        G_ExitLevel();
        stage = 6;
    }
    else if (stage == 6 && gamestate == GS_LEVEL_SELECT && state->completed)
    {
        if (mode == 2 && M_FileExists(save_path))
            I_Error("APMINE_GAMEPLAY: completed pistol-resume map retained a stale level save");
        printf("APMINE_GAMEPLAY: PASS mode=%d game=%s pickup=%d key=%d completion_recorded=1\n",
               mode, gamemode == commercial ? "doom2" : "doom", pickup_index, key_index);
        I_Quit();
    }
}

#endif
