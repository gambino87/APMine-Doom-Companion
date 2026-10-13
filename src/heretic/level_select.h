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

#ifndef __LEVEL_SELECT__
#define __LEVEL_SELECT__


#include "doomtype.h"
#include "d_event.h"


boolean LevelSelectResponder(event_t* ev);
void DrawLevelSelect();
void ShowLevelSelect();
void TickLevelSelect();


#endif
