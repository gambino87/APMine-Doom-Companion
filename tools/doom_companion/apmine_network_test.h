/* SPDX-License-Identifier: GPL-3.0-only */
/* APMine companion change, 2026-10-12: owned loopback network acceptance. */
#ifndef APMINE_NETWORK_TEST_H
#define APMINE_NETWORK_TEST_H

#include "apmine_gameplay_test.h"

static int APMineNetworkMarker(const char *path, const char *expected)
{
    char value[64] = {0};
    FILE *file = M_fopen(path, "rb");
    size_t length = file ? fread(value, 1, sizeof(value) - 1, file) : 0;
    if (file) fclose(file);
    return length == strlen(expected) && !strcmp(value, expected);
}

static int APMineNetworkLoopback(const char *address)
{
    char *end;
    long port;
    if (strncmp(address, "127.0.0.1:", 10)) return 0;
    if (!address[10]) return 0;
    port = strtol(address + 10, &end, 10);
    return !*end && port >= 1024 && port <= 65535;
}

static mobj_t *APMineNetworkFindPickup(ap_level_index_t level)
{
    ap_level_info_t *info = ap_get_level_info(level);
    thinker_t *thinker;
    for (thinker = thinkercap.next; thinker != &thinkercap; thinker = thinker->next)
    {
        mobj_t *object;
        int doom_type;
        if (thinker->function.acp1 != (actionf_p1)P_MobjThinker) continue;
        object = (mobj_t *)thinker;
        if (!(object->flags & MF_SPECIAL) || object->index < 0 || object->index >= info->thing_count) continue;
        doom_type = info->thing_infos[object->index].doom_type;
        if (APMineKeyCard(doom_type) < 0 && ap_validate_doom_location(level, doom_type, object->index) == 1
                && !ap_is_vanilla_weapon_location(level, object->index)
                && !ap_is_location_checked(level, object->index))
            return object;
    }
    return NULL;
}

static void APMineNetworkVitals(player_t *player, int health, int bullets, boolean chainsaw)
{
    if (ap_state.player_state.max_health != health || ap_state.player_state.max_armor != 205
            || ap_state.player_state.max_ammo[am_clip] != bullets
            || ap_state.enhanced_backpack_count != 1
            || !ap_state.player_state.weapon_owned[wp_shotgun]
            || !ap_state.player_state.weapon_owned[wp_chaingun]
            || !!ap_state.player_state.weapon_owned[wp_chainsaw] != !!chainsaw)
        I_Error("APMINE_NETWORK: AP inventory did not match the owned fixture");
    if (player && (player->maxammo[am_clip] != bullets || !player->weaponowned[wp_shotgun]
            || !player->weaponowned[wp_chaingun] || !!player->weaponowned[wp_chainsaw] != !!chainsaw))
        I_Error("APMINE_NETWORK: received inventory did not reach the active player");
}

static void APMineNetworkStep(void)
{
    static int stage, frames, map_frames, reconnect, pickup_index, key_index, key_card;
    static int health, armor, ammo[NUMAMMO], weapons[NUMWEAPONS];
    static angle_t angle;
    static char save_path[260];
    int parameter = M_CheckParmWithArgs("-apmine-network-test", 1);
    ap_level_index_t level = ap_make_level_index(1, 2);
    ap_level_state_t *state;
    player_t *player = &players[consoleplayer];
    int i;
    if (!parameter) return;
    if (++frames > 1800) I_Error("APMINE_NETWORK: finite fixture timed out at stage %d", stage);
    state = ap_get_level_state(level);
    if (!stage)
    {
        int server = M_CheckParmWithArgs("-apserver", 1);
        if (!server || !APMineNetworkLoopback(myargv[server + 1])
                || ap_practice_mode || ap_force_disable_behaviors || !nomonsters
                || !getenv("SDL_VIDEODRIVER") || strcmp(getenv("SDL_VIDEODRIVER"), "dummy")
                || !APMineNetworkMarker("APMine-private-network.marker", "APMine-private-network-v1\n"))
            I_Error("APMINE_NETWORK: requires an owned dummy-SDL loopback fixture");
        if (!strcmp(myargv[parameter + 1], "initial")) reconnect = 0;
        else if (!strcmp(myargv[parameter + 1], "reconnect")) reconnect = 1;
        else I_Error("APMINE_NETWORK: unknown private network case");
        if (!state->unlocked || ap_state.pistol_start || !ap_state.global_progressive_weapons)
            I_Error("APMINE_NETWORK: unexpected owned fixture options/map access");
        APMineNetworkVitals(NULL, reconnect ? 203 : 202, reconnect ? 220 : 215, reconnect);
        play_level(level.ep, level.map);
        stage = 1;
        return;
    }
    if (stage == 1 && gamestate == GS_LEVEL && gameaction == ga_nothing && gamemap == 2 && leveltime >= 8)
    {
        mobj_t *pickup;
        if (++map_frames < 8) return;
        APMineNetworkVitals(player, reconnect ? 203 : 202, reconnect ? 220 : 215, reconnect);
        V_ScreenShot("APNET%02i.%s");
        if (reconnect)
        {
            if (!state->completed) I_Error("APMINE_NETWORK: reconnect lost map completion");
            printf("APMINE_NETWORK: PASS reconnect health=203 bullets=220 completed=1\n");
            I_Quit();
        }
        pickup = APMineNetworkFindPickup(level);
        if (!pickup) I_Error("APMINE_NETWORK: no spawned AP pickup");
        pickup_index = pickup->index;
        APMineTouchPickup(pickup, player);
        printf("APMINE_NETWORK: pickup=%d\n", pickup_index);
        fflush(stdout);
        stage = 2;
    }
    else if (stage == 2 && ap_is_location_checked(level, pickup_index))
    {
        mobj_t *key = APMineFindPickup(level, true);
        if (!key) I_Error("APMINE_NETWORK: no spawned key");
        key_index = key->index;
        key_card = APMineKeyCard(ap_get_level_info(level)->thing_infos[key_index].doom_type);
        APMineTouchPickup(key, player);
        printf("APMINE_NETWORK: key=%d\n", key_index);
        fflush(stdout);
        stage = 3;
    }
    else if (stage == 3 && player->cards[key_card] && state->keys[key_card % 3]
            && ap_is_location_checked(level, key_index) && ap_state.player_state.max_health == 203
            && ap_state.player_state.max_ammo[am_clip] == 220 && player->weaponowned[wp_chainsaw])
    {
        APMineNetworkVitals(player, 203, 220, true);
        health = player->health > 13 ? player->health - 13 : 1;
        player->health = player->mo->health = health;
        armor = player->armorpoints;
        angle = player->mo->angle + ANG90;
        player->mo->angle = angle;
        for (i = 0; i < NUMAMMO; ++i) ammo[i] = player->ammo[i];
        for (i = 0; i < NUMWEAPONS; ++i) weapons[i] = player->weaponowned[i];
        snprintf(save_path, sizeof(save_path), gamemode == commercial ? "%s/save_MAP02.dsg" : "%s/save_E1M2.dsg", apdoom_get_save_dir());
        G_LevelSelect();
        stage = 4;
    }
    else if (stage == 4 && gamestate == GS_LEVEL_SELECT && gameaction == ga_nothing)
    {
        if (!M_FileExists(save_path)) I_Error("APMINE_NETWORK: hub did not create a native map save");
        player->health = player->mo->health = 1;
        player->mo->angle += ANG90;
        play_level(level.ep, level.map);
        stage = 5;
    }
    else if (stage == 5 && gamestate == GS_LEVEL && gameaction == ga_nothing && gamemap == 2)
    {
        if (player->health != health || player->armorpoints != armor || player->mo->angle != angle
                || !player->cards[key_card] || !ap_is_location_checked(level, pickup_index))
            I_Error("APMINE_NETWORK: native return did not preserve state");
        for (i = 0; i < NUMAMMO; ++i)
            if (player->ammo[i] != ammo[i]) I_Error("APMINE_NETWORK: return lost ammo");
        for (i = 0; i < NUMWEAPONS; ++i)
            if (player->weaponowned[i] != weapons[i]) I_Error("APMINE_NETWORK: return lost weapons");
        G_ExitLevel();
        stage = 6;
    }
    else if (stage == 6 && gamestate == GS_LEVEL_SELECT && state->completed
            && APMineNetworkMarker("APMine-server-checks-confirmed.marker", "APMine-network-checks-v1\n"))
    {
        printf("APMINE_NETWORK: PASS initial health=203 bullets=220 pickup=%d key=%d completed=1\n", pickup_index, key_index);
        I_Quit();
    }
}

#endif
