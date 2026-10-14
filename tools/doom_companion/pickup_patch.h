// APMine companion change, 2026-10-13: original category symbols and runtime compositing.
// Copyright (C) 2026 APMine contributors
// SPDX-License-Identifier: GPL-3.0-only

#ifndef APMINE_PICKUP_PATCH_H
#define APMINE_PICKUP_PATCH_H

#include <stdint.h>
#include <stdlib.h>
#include <string.h>

enum { APMINE_PICKUP_FILLER, APMINE_PICKUP_USEFUL, APMINE_PICKUP_PROGRESSION };

static int APMinePickupCategoryFromFlags(unsigned int flags)
{
    return (flags & 1) ? APMINE_PICKUP_PROGRESSION
         : (flags & 2) ? APMINE_PICKUP_USEFUL : APMINE_PICKUP_FILLER;
}

static unsigned int APMinePatchRead16(const unsigned char *p)
{
    return p[0] | ((unsigned int)p[1] << 8);
}

static uint32_t APMinePatchRead32(const unsigned char *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) | ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void APMinePatchWrite16(unsigned char *p, int value)
{
    p[0] = (unsigned char)value;
    p[1] = (unsigned char)((unsigned int)value >> 8);
}

static void APMinePatchWrite32(unsigned char *p, uint32_t value)
{
    int i;
    for (i = 0; i < 4; ++i) p[i] = (unsigned char)(value >> (i * 8));
}

static void APMinePaintPickupSymbol(int16_t *pixels, int width, int height,
                                  int origin_x, int origin_y, int category,
                                  unsigned char color, unsigned char outline)
{
    static const char *const symbols[3][7] = {
        {".........", "...###...", "..#####..", "..#####..", "..#####..", "...###...", "........."},
        {"...###...", "...###...", ".#######.", ".#######.", ".#######.", "...###...", "...###..."},
        {"....#....", "...#.#...", "..#...#..", ".........", "....#....", "...#.#...", "..#...#.."}
    };
    int x, y, dx, dy;
    for (y = 0; y < 7; ++y)
        for (x = 0; x < 9; ++x)
            if (symbols[category][y][x] == '#')
                for (dy = -1; dy <= 1; ++dy)
                    for (dx = -1; dx <= 1; ++dx)
                    {
                        int px = origin_x + x + dx, py = origin_y + y + dy;
                        if (px >= 0 && px < width && py >= 0 && py < height)
                            pixels[py * width + px] = outline;
                    }
    for (y = 0; y < 7; ++y)
        for (x = 0; x < 9; ++x)
            if (symbols[category][y][x] == '#')
                pixels[(origin_y + y) * width + origin_x + x] = color;
}

// Returned patch is malloc-owned. The base bytes are only read, never modified.
static unsigned char *APMineComposePickupPatch(const unsigned char *base, size_t size,
                                              int category, unsigned char color,
                                              unsigned char outline, size_t *output_size)
{
    int width = 13, height = 13, left = 6, top = 16;
    int base_width = 0, base_height = 0, base_x = 0, x, y;
    int16_t *pixels;
    unsigned char *result;
    size_t capacity, used;
    if (!output_size) return NULL;
    *output_size = 0;
    if (category < 0 || category > 2) return NULL;
    if (base)
    {
        if (size < 8) return NULL;
        base_width = APMinePatchRead16(base);
        base_height = APMinePatchRead16(base + 2);
        if (base_width < 1 || base_width > 1024 || base_height < 1 || base_height > 247
            || size < 8 + (size_t)base_width * 4) return NULL;
        width = base_width < 11 ? 11 : base_width;
        height = base_height + 8;
        base_x = (width - base_width) / 2;
        left = (int)(int16_t)APMinePatchRead16(base + 4) + base_x;
        top = (int)(int16_t)APMinePatchRead16(base + 6) + 8;
        if (left < -32768 || left > 32767 || top < -32768 || top > 32767) return NULL;
    }
    else if (size) return NULL;
    pixels = (int16_t *)malloc((size_t)width * height * sizeof(*pixels));
    if (!pixels) return NULL;
    for (y = 0; y < width * height; ++y) pixels[y] = -1;
    if (base)
    {
        for (x = 0; x < base_width; ++x)
        {
            size_t position = APMinePatchRead32(base + 8 + x * 4);
            int last_top = -1, posts = 0;
            if (position < 8 + (size_t)base_width * 4 || position >= size) goto invalid;
            while (base[position] != 255)
            {
                int post_top, length;
                if (++posts > base_height + 1 || size - position < 4) goto invalid;
                post_top = base[position];
                if (post_top <= last_top) post_top += last_top;
                last_top = post_top;
                length = base[position + 1];
                if ((size_t)length > size - position - 4 || post_top + length > base_height) goto invalid;
                for (y = 0; y < length; ++y)
                    pixels[(post_top + y + 8) * width + base_x + x] = base[position + 3 + y];
                position += (size_t)length + 4;
                if (position >= size) goto invalid;
            }
        }
    }
    APMinePaintPickupSymbol(pixels, width, height, (width - 9) / 2, base ? 0 : 3, category, color, outline);
    capacity = 8 + (size_t)width * 4 + (size_t)width * (height * 5 + 1);
    result = (unsigned char *)malloc(capacity);
    if (!result) goto invalid;
    APMinePatchWrite16(result, width);
    APMinePatchWrite16(result + 2, height);
    APMinePatchWrite16(result + 4, left);
    APMinePatchWrite16(result + 6, top);
    used = 8 + (size_t)width * 4;
    for (x = 0; x < width; ++x)
    {
        APMinePatchWrite32(result + 8 + x * 4, (uint32_t)used);
        y = 0;
        while (y < height)
        {
            int start, length, i;
            if (pixels[y * width + x] < 0) { ++y; continue; }
            start = y;
            while (y < height && pixels[y * width + x] >= 0) ++y;
            length = y - start;
            result[used++] = (unsigned char)start;
            result[used++] = (unsigned char)length;
            result[used++] = 0;
            for (i = start; i < y; ++i) result[used++] = (unsigned char)pixels[i * width + x];
            result[used++] = 0;
        }
        result[used++] = 255;
    }
    free(pixels);
    *output_size = used;
    return result;
invalid:
    free(pixels);
    return NULL;
}

#endif
