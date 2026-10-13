/* SPDX-License-Identifier: GPL-3.0-only */
/* APMine companion change, 2026-10-12: initialization handshake. */
/* Private launch-path reservation and complete initialization-result parsing. */
#ifndef LN_HANDSHAKE_H
#define LN_HANDSHAKE_H

#include <errno.h>
#include <fcntl.h>
#include <stdio.h>
#include <string.h>
#include <sys/stat.h>

#ifdef _WIN32
#include <io.h>
#include <process.h>
#define LN_INIT_PID() ((unsigned long)_getpid())
#define LN_INIT_OPEN(path) _open(path, _O_CREAT | _O_EXCL | _O_WRONLY | _O_BINARY, _S_IREAD | _S_IWRITE)
#define LN_INIT_CLOSE(fd) _close(fd)
#else
#include <unistd.h>
#define LN_INIT_PID() ((unsigned long)getpid())
#define LN_INIT_OPEN(path) open(path, O_CREAT | O_EXCL | O_WRONLY, 0600)
#define LN_INIT_CLOSE(fd) close(fd)
#endif

static int LN_ReserveInitFile(const char *prefix, char *path, size_t capacity)
{
    static unsigned int sequence = 0;
    for (int attempt = 0; attempt < 1024; ++attempt)
    {
        int length = snprintf(path, capacity, "%s%08lx-%08x.tmp", prefix, LN_INIT_PID(), sequence++);
        if (length < 0 || (size_t)length >= capacity)
            return -1;
        int fd = LN_INIT_OPEN(path);
        if (fd >= 0)
        {
            if (LN_INIT_CLOSE(fd) != 0)
            {
                remove(path);
                return -1;
            }
            return 0;
        }
        if (errno != EEXIST)
            return -1;
    }
    return -1;
}

/* Zero means pending; only a complete, bounded line is copied to the result. */
static int LN_ReadInitFile(const char *path, char *result, size_t capacity)
{
    char raw[34];
    FILE *file = fopen(path, "rb");
    if (!file)
        return 0;
    size_t count = fread(raw, 1, sizeof(raw), file);
    int failed = ferror(file);
    fclose(file);
    if (failed)
        return 0;
    const char *newline = (const char *)memchr(raw, '\n', count);
    if (!newline)
        return count == sizeof(raw) ? -1 : 0;
    size_t length = (size_t)(newline - raw);
    if (length > 0 && raw[length - 1] == '\r')
        --length;
    if (length == 0 || length >= capacity || (size_t)(newline - raw) + 1 != count)
        return -1;
    for (size_t index = 0; index < length; ++index)
        if (!((raw[index] >= 'A' && raw[index] <= 'Z') || (raw[index] >= 'a' && raw[index] <= 'z')))
            return -1;
    memcpy(result, raw, length);
    result[length] = '\0';
    return 1;
}

#undef LN_INIT_PID
#undef LN_INIT_OPEN
#undef LN_INIT_CLOSE
#endif
