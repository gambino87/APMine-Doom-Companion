// APMine companion change, 2026-10-13: compose original category badges at sprite initialization.
// Copyright (C) 2026 APMine contributors
// SPDX-License-Identifier: GPL-3.0-only

#include <stdio.h>
#include <string.h>
#include <limits.h>
#include "i_system.h"
#include "w_wad.h"
#include "z_zone.h"
#include "v_trans.h"
#include "doomstat.h"
#include "pickup_patch.h"
#include "pickup_sprites.h"

static const char *const aliases[][2] = {
    {"APBH", "BON1"}, {"APBA", "BON2"}, {"APAR", "ARM1"}, {"APMA", "ARM2"},
    {"APSC", "SOUL"}, {"APMS", "MEGA"}, {"APIN", "PINV"}, {"APIS", "PINS"},
    {"APBE", "PSTR"}, {"APCL", "CLIP"}, {"APAM", "AMMO"}, {"APRK", "ROCK"},
    {"APBR", "BROK"}, {"APCE", "CELL"}, {"APCP", "CELP"}, {"APSH", "SHEL"},
    {"APSB", "SBOX"}, {"APBP", "BPAK"}
};

typedef struct {
    patch_t *variants[3];
} pickup_cache_t;

static pickup_cache_t *cache;
static unsigned int cache_count;
static int colors[3], outline;

int APMineIsPickupSpriteLump(int lump)
{
    const char *name;
    size_t i;
    if (lump < 0 || (unsigned int)lump >= numlumps) return 0;
    name = lumpinfo[lump]->name;
    if (name[4] < 'A' || name[4] > 'G' || name[5] != '0' || name[6]) return 0;
    if (!strncmp(name, "APJI", 4) || !strncmp(name, "APPI", 4)) return 1;
    for (i = 0; i < sizeof(aliases) / sizeof(aliases[0]); ++i)
        if (!strncmp(name, aliases[i][0], 4)) return 1;
    return 0;
}

patch_t *APMinePickupSpritePatch(int lump, int category)
{
    const unsigned char *base = NULL;
    size_t base_size = 0, i;
    int source_lump = -1;
    const char *name;
    if (!APMineIsPickupSpriteLump(lump)) return W_CacheLumpNum(lump, PU_CACHE);
    name = lumpinfo[lump]->name;
    if (category < 0 || category > 2) category = !strncmp(name, "APPI", 4) ? 2 : 0;
    if (!cache)
    {
        byte *palette = W_CacheLumpName("PLAYPAL", PU_STATIC);
        if (W_LumpLength(W_GetNumForName("PLAYPAL")) < 768) I_Error("Invalid pickup palette");
        cache_count = numlumps;
        cache = calloc(cache_count, sizeof(*cache));
        if (!cache) I_Error("Could not allocate pickup sprite cache");
        colors[0] = V_GetPaletteIndex(palette, 176, 176, 176);
        colors[1] = V_GetPaletteIndex(palette, 72, 220, 112);
        colors[2] = V_GetPaletteIndex(palette, 252, 204, 56);
        outline = V_GetPaletteIndex(palette, 24, 24, 24);
        printf("APMinePickupSprites: native-composite-v1\n");
    }
    if (numlumps != cache_count) I_Error("Pickup sprite lump directory changed after initialization");
    if (!cache[lump].variants[0])
    {
        int variant;
        for (i = 0; i < sizeof(aliases) / sizeof(aliases[0]); ++i)
            if (!strncmp(name, aliases[i][0], 4))
            {
                char source[9] = {0};
                memcpy(source, aliases[i][1], 4);
                memcpy(source + 4, name + 4, 2);
                source_lump = W_CheckNumForName(source);
                // DOOM has no MegaSphere; its unused alias needs only our symbol.
                if (source_lump < 0)
                {
                    if (gamemode == commercial || strcmp(aliases[i][1], "MEGA"))
                        I_Error("Player WAD is missing pickup sprite %s", source);
                }
                else
                {
                    base_size = W_LumpLength(source_lump);
                    base = W_CacheLumpNum(source_lump, PU_STATIC);
                }
                break;
            }
        for (variant = 0; variant < 3; ++variant)
        {
            size_t size;
            unsigned char *patch = APMineComposePickupPatch(base, base_size, variant,
                                                           colors[variant], outline, &size);
            if (!patch || size > INT_MAX) I_Error("Could not compose pickup sprite %.8s", name);
            cache[lump].variants[variant] = Z_Malloc((int)size, PU_STATIC, NULL);
            memcpy(cache[lump].variants[variant], patch, size);
            free(patch);
        }
        if (source_lump >= 0) W_ReleaseLumpNum(source_lump);
    }
    return cache[lump].variants[category];
}

patch_t *APMinePickupIconPatch(int lump)
{
    // Named AP aliases in receipts/shop refer to useful stat/ammo upgrades;
    // generic progression/filler symbols keep their own default category.
    const char *name = lumpinfo[lump]->name;
    int category = (!strncmp(name, "APJI", 4) || !strncmp(name, "APPI", 4)) ? -1 : 1;
    return APMinePickupSpritePatch(lump, category);
}
