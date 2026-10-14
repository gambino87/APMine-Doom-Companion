// APMine companion change, 2026-10-13: bounded map-menu icon layout.
// Copyright (C) 2026 APMine contributors
// SPDX-License-Identifier: GPL-3.0-only

#ifndef APMINE_LEVEL_SELECT_LAYOUT_H
#define APMINE_LEVEL_SELECT_LAYOUT_H

enum
{
    APMINE_WEAPON_SPACING = 38,
    APMINE_WEAPON_MAX_WIDTH = 28,
    APMINE_WEAPON_MAX_HEIGHT = 12,
    APMINE_WEAPON_BOTTOM = 198,
    APMINE_MAP_KEY_X = 20
};

static void APMineLevelSelectWeaponScale(int width, int height, int *num, int *den)
{
    *num = 3;
    *den = 4;
    if (width * *num > APMINE_WEAPON_MAX_WIDTH * *den)
    {
        *num = APMINE_WEAPON_MAX_WIDTH;
        *den = width;
    }
    if (height * *num > APMINE_WEAPON_MAX_HEIGHT * *den)
    {
        *num = APMINE_WEAPON_MAX_HEIGHT;
        *den = height;
    }
}

static int APMineLevelSelectListY(int y, int first_y, int last_y)
{
    if (last_y <= first_y)
        return 26;
    return 26 + ((y - first_y) * (172 - 26)) / (last_y - first_y);
}

#endif
