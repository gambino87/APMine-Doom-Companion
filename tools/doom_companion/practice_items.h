/* SPDX-License-Identifier: GPL-3.0-only */
/* APMine companion change, 2026-10-12: AP-ID practice reward resolution. */
#ifndef APMINE_PRACTICE_ITEMS_H
#define APMINE_PRACTICE_ITEMS_H

#include <stdint.h>

/* AP item IDs are not Doom thing IDs. Preserve global-item precedence, then
   resolve map-specific rewards using the definitions' one-based level fields. */
template <typename Table>
static int64_t APMinePracticeItemId(const Table& table, int doom_type, int episode, int map)
{
    int64_t scoped = 0;
    for (const auto& entry : table)
    {
        const auto& item = entry.second;
        if (item.doom_type != doom_type)
            continue;
        if (item.ep == -1 && item.map == -1)
            return entry.first;
        if (!scoped && item.ep == episode && item.map == map)
            scoped = entry.first;
    }
    return scoped;
}

#endif
