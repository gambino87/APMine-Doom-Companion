// APMine companion change, 2026-10-13: cached, player-derived pickup rendering.
// Copyright (C) 2026 APMine contributors
// SPDX-License-Identifier: GPL-3.0-only

#ifndef APMINE_PICKUP_SPRITES_H
#define APMINE_PICKUP_SPRITES_H

#include "v_patch.h"

int APMineIsPickupSpriteLump(int lump);
patch_t *APMinePickupSpritePatch(int lump, int category);
patch_t *APMinePickupIconPatch(int lump);

#endif
