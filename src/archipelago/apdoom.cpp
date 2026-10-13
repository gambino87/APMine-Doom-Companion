// APMine companion change, 2026-10-12: external resources and practice reward lookup.
//
// Copyright(C) 2023 David St-Louis
// Copyright(C) 2025 Kay "Kaito" Sinclaire
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
// Primary source file for interfacing with Archipelago.
//

#ifdef _WIN32
#define NOMINMAX
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <io.h>
#ifdef _MSC_VER
#include <direct.h>
#endif
#else
#include <sys/types.h>
#include <sys/stat.h>
#endif


#include "apdoom.h"
#include "Archipelago.h"
#include <json/json.h>
#include <algorithm>
#include <filesystem>
#include <memory.h>
#include <stdarg.h>
#include <chrono>
#include <thread>
#include <vector>
#include <fstream>
#include <sstream>
#include <set>
#include <map>
#include <atomic>

#include "local.hpp"
#include "apzip.h"

extern "C" int W_CheckNumForName(const char *name);


static Json::Value AP_ReadJson(const char *data, size_t size)
{
	static Json::CharReaderBuilder builder;
	std::unique_ptr<Json::CharReader> reader(builder.newCharReader());

	Json::Value json;
	reader->parse(data, data+size, &json, NULL);
	return json;
}


static Json::Value AP_ReadJson(const std::string &data)
{
	return AP_ReadJson(data.data(), data.size());
}


enum class ap_game_t
{
	doom,
	doom2,
	heretic
};


const ap_worldinfo_t *ap_world_info;
ap_gameinfo_t ap_game_info;
ap_state_t ap_state;
int ap_is_in_game = 0;
int ap_episode_count = -1;

int ap_race_mode = false; // Server reports this is a race, not casual.
int ap_practice_mode = false; // Not connected to a server, simulate play.
int ap_force_disable_behaviors = false; // For demo compatibility.
long int initial_timestamp = 0; // Time that the seed was started.

static bool detected_old_apworld = false;

static ap_game_t ap_base_game;
static int ap_weapon_count = -1;
static int ap_ammo_count = -1;
static int ap_powerup_count = -1;
static int ap_inventory_count = -1;

static const std::map<int, int>& get_weapons_map();
const std::map<int, int>& get_keys_map();
static int max_map_count = -1;
static ap_settings_t ap_settings;
static AP_RoomInfo ap_room_info;
static std::vector<int64_t> ap_item_queue; // We queue when we're in the menu.
static bool ap_was_connected = false; // Got connected at least once. That means the state is valid
static constexpr int AP_ITEM_FLAG_PROGRESSION = 1;
static constexpr int AP_ITEM_FLAG_USEFUL = 2;
static std::set<int64_t> ap_progressive_locations;
static std::set<int64_t> ap_useful_locations;
static std::set<int64_t> ap_scouted_locations;
static std::map<int64_t, int> ap_scouted_location_item_doom_types;
static std::set<int64_t> suppressed_locations; // Locations that don't exist in current multiworld (checksanity, etc)
static std::atomic_bool suppressed_locations_received{false};
static std::set<int> global_logic_weapon_doom_types;
static std::map<std::string, std::set<int>> level_logic_weapon_doom_types;
static std::set<int64_t> vanilla_key_locations; // Suppressed AP locations that should still spawn vanilla keys.
static std::set<int64_t> vanilla_weapon_locations; // Suppressed AP locations that should still spawn vanilla weapons.
static std::map<int64_t, int> randomized_pickup_location_types; // Location id -> generated pickup rando output type
static std::map<int64_t, int> enhanced_bonus_location_types; // Location id -> vanilla bonus type/effect
static std::map<int64_t, int> enhanced_powerup_location_types; // Location id -> vanilla powerup type/effect
static std::map<int64_t, int> enhanced_ammo_location_types; // Location id -> vanilla/effective ammo type
static bool ap_initialized = false;
static std::vector<std::string> ap_cached_messages;
static std::string ap_seed_string;
static std::vector<ap_notification_icon_t> ap_notification_icons;
static bool ap_items_synced = false;
static ap_level_index_t current_level_index = {0, 0};

static AP_GetServerDataRequest race_mode_request; // Required to fetch from server

static std::filesystem::path ap_save_path;

#define SLOT_DATA_CALLBACK(func_name, output, condition) void func_name (int result) { if (condition) output = result; }

static void ap_debug_log_recv(const std::string& text)
{
	if (ap_save_path.empty())
		return;

	std::ofstream out(ap_save_path / "ap-receive-debug.log", std::ios::app);
	if (out.is_open())
		out << text << "\n";
}

SLOT_DATA_CALLBACK(f_difficulty, ap_state.difficulty, (!ap_settings.override_skill) );
SLOT_DATA_CALLBACK(f_random_monsters, ap_state.random_monsters, (!ap_settings.override_monster_rando) );
SLOT_DATA_CALLBACK(f_random_items, ap_state.random_items, (!ap_settings.override_item_rando) );
SLOT_DATA_CALLBACK(f_random_music, ap_state.random_music, (!ap_settings.override_music_rando) );
SLOT_DATA_CALLBACK(f_flip_levels, ap_state.flip_levels, (!ap_settings.override_flip_levels) );
SLOT_DATA_CALLBACK(f_pistol_start, ap_state.pistol_start, true);
SLOT_DATA_CALLBACK(f_resume_pistol_start_levels, ap_state.resume_pistol_start_levels, true);
SLOT_DATA_CALLBACK(f_vanilla_powerup_locations, ap_state.vanilla_powerup_locations, true);
SLOT_DATA_CALLBACK(f_enhanced_ap_bonus_items, ap_state.enhanced_ap_bonus_items, true);
SLOT_DATA_CALLBACK(f_enhanced_ap_powerups, ap_state.enhanced_ap_powerups, true);
SLOT_DATA_CALLBACK(f_enhanced_ammo, ap_state.enhanced_ammo, true);
SLOT_DATA_CALLBACK(f_remove_chainsaws_from_game, ap_state.remove_chainsaws_from_game, true);
SLOT_DATA_CALLBACK(f_level_specific_weapons, ap_state.level_specific_weapons, true);
SLOT_DATA_CALLBACK(f_progressive_weapons, ap_state.progressive_weapons, true);
SLOT_DATA_CALLBACK(f_global_progressive_weapons, ap_state.global_progressive_weapons, true);
SLOT_DATA_CALLBACK(f_vanilla_keys_mode, ap_state.vanilla_keys_mode, true);
SLOT_DATA_CALLBACK(f_keys_give_map_access, ap_state.keys_give_map_access, true);
SLOT_DATA_CALLBACK(f_release_keys_for_completed_levels, ap_state.release_keys_for_completed_levels, true);
SLOT_DATA_CALLBACK(f_release_computer_area_map_for_completed_levels, ap_state.release_computer_area_map_for_completed_levels, true);
SLOT_DATA_CALLBACK(f_release_weapons_for_completed_levels, ap_state.release_weapons_for_completed_levels, true);
SLOT_DATA_CALLBACK(f_reset_level_on_death, ap_state.reset_level_on_death, (!ap_settings.override_reset_level_on_death) );
SLOT_DATA_CALLBACK(f_require_100_percent_kills_to_exit, ap_state.require_100_percent_kills_to_exit, true);
SLOT_DATA_CALLBACK(f_keep_completed_maps_cleared, ap_state.keep_completed_maps_cleared, true);
SLOT_DATA_CALLBACK(f_show_monsters_on_automap, ap_state.show_monsters_on_automap, true);

void f_goal(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	if (json.isInt())
	{
		detected_old_apworld = true;
		return;
	}

	ap_state.goal = json["type"].asInt();
	switch (ap_state.goal)
	{
	case 3: // Specific Levels
	case 2: // Random Levels
		{
			ap_state.goal_level_count = (int)json["levels"].size();
			ap_state.goal_level_list = new ap_level_index_t[ap_state.goal_level_count];
			for (int i = 0; i < ap_state.goal_level_count; ++i)
			{
				const Json::Value& j_idx = json["levels"][i];
				ap_state.goal_level_list[i].ep = j_idx[0].asInt() - 1;
				ap_state.goal_level_list[i].map = j_idx[1].asInt() - 1;
			}
		}
		break;
	case 1: // Some number of levels
			ap_state.goal_level_count = json["count"].asInt();
		break;
	default:
		break;
	}
}

void f_suppressed_locations(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	suppressed_locations.clear();
	for (const Json::Value& loc_id : json)
		suppressed_locations.insert(loc_id.asInt());
	suppressed_locations_received = true;
}

static void clear_vanilla_key_level_state()
{
	for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
	{
		ap_level_state_t *level_state = ap_get_level_state(*idx);
		if (!level_state)
			continue;

		level_state->keys[0] = 0;
		level_state->keys[1] = 0;
		level_state->keys[2] = 0;
	}
}

void f_logic_weapon_types(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	global_logic_weapon_doom_types.clear();
	level_logic_weapon_doom_types.clear();

	if (!json.isObject())
		return;

	for (const std::string& key : json.getMemberNames())
	{
		const Json::Value& doom_types = json[key];
		std::set<int>* target = (key == "global")
			? &global_logic_weapon_doom_types
			: &level_logic_weapon_doom_types[key];

		if (!doom_types.isArray())
			continue;

		for (const Json::Value& doom_type : doom_types)
			target->insert(doom_type.asInt());
	}
}

void f_vanilla_key_locations(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	vanilla_key_locations.clear();
	for (const Json::Value& loc_id : json)
		vanilla_key_locations.insert(loc_id.asInt());
	if (!vanilla_key_locations.empty())
	{
		ap_state.vanilla_keys_mode = 1;
		clear_vanilla_key_level_state();
	}
}

void f_vanilla_weapon_locations(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	vanilla_weapon_locations.clear();
	for (const Json::Value& loc_id : json)
		vanilla_weapon_locations.insert(loc_id.asInt());
}

void f_enhanced_bonus_locations(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	enhanced_bonus_location_types.clear();
	for (const std::string& loc_id : json.getMemberNames())
		enhanced_bonus_location_types[std::stoll(loc_id)] = json[loc_id].asInt();
}

void f_enhanced_powerup_locations(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	enhanced_powerup_location_types.clear();
	for (const std::string& loc_id : json.getMemberNames())
		enhanced_powerup_location_types[std::stoll(loc_id)] = json[loc_id].asInt();
	if (!enhanced_powerup_location_types.empty())
		ap_state.enhanced_ap_powerups = 1;
}

void f_enhanced_ammo_locations(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	enhanced_ammo_location_types.clear();
	for (const std::string& loc_id : json.getMemberNames())
		enhanced_ammo_location_types[std::stoll(loc_id)] = json[loc_id].asInt();
	if (!enhanced_ammo_location_types.empty())
		ap_state.enhanced_ammo = 1;
}

void f_randomized_pickup_locations(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	randomized_pickup_location_types.clear();
	for (const std::string& loc_id : json.getMemberNames())
		randomized_pickup_location_types[std::stoll(loc_id)] = json[loc_id].asInt();
}

void f_episodes(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	for (const Json::Value& episode : json)
	{
		int ep_int = episode.asInt() - 1;
		if (ep_int >= 0 && ep_int < ap_episode_count)
			ap_state.episodes[ep_int] = 1;
	}
}

static void set_episode_enabled(int episode, int enabled)
{
	int ep_int = episode - 1;
	if (ep_int >= 0 && ep_int < ap_episode_count)
		ap_state.episodes[ep_int] = enabled ? 1 : 0;
}

void f_episode1(int enabled) { set_episode_enabled(1, enabled); }
void f_episode2(int enabled) { set_episode_enabled(2, enabled); }
void f_episode3(int enabled) { set_episode_enabled(3, enabled); }
void f_episode4(int enabled) { set_episode_enabled(4, enabled); }

void f_ammo_start(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	for (int i = 0; i < ap_ammo_count && i < (int)json.size(); ++i)
		ap_state.max_ammo_start[i] = json[i].asInt();
}

void f_ammo_add(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	for (int i = 0; i < ap_ammo_count && i < (int)json.size(); ++i)
		ap_state.max_ammo_add[i] = json[i].asInt();
}

void f_ammo_steps(std::string json_blob)
{
	Json::Value json = AP_ReadJson(json_blob);
	for (int i = 0; i < ap_ammo_count && i < (int)json.size(); ++i)
		ap_state.max_ammo_steps[i] = json[i].asInt();
}

void f_small_health_upgrade(int value) { if (value > 0) ap_state.small_health_upgrade = value; }
void f_small_armor_upgrade(int value) { if (value > 0) ap_state.small_armor_upgrade = value; }
void f_health_upgrade(int value) { if (value > 0) ap_state.health_upgrade = value; }
void f_large_health_upgrade(int value) { if (value > 0) ap_state.large_health_upgrade = value; }
void f_armor_upgrade(int value) { if (value > 0) ap_state.armor_upgrade = value; }
void f_large_armor_upgrade(int value) { if (value > 0) ap_state.large_armor_upgrade = value; }
void f_mega_upgrade(int value) { if (value > 0) ap_state.mega_upgrade = value; }

void f_itemclr();
void f_itemrecv(int64_t item_id, bool notify_player);
void f_itemrecv_apcpp(int64_t item_id, int player, bool notify_player);
void f_locrecv(int64_t loc_id);
void f_locinfo(std::vector<AP_NetworkItem> loc_infos);
void f_deathlink(std::string source, std::string cause);
void f_deathlink_apcpp();
void f_energylink(std::string json_blob);
void load_state();
void save_state();
void APSend(std::string msg);

static void ap_fake_item_msg(int item_id, const char *sender);
static void ap_energylink_pool_update(void);

// ===== PWAD SUPPORT =========================================================
// All of these are loaded from json on game startup.
// The following are large tables of game data, usually auto-generated by ap_gen_tool,
// that are parsed in gamedata.cpp.

static level_select_storage_t level_select_screens; // index:screen_num<ap_levelselect_t>
static map_tweaks_storage_t map_tweak_list; // <int episode, <int map, <ap_maptweak_t>>>
static level_info_storage_t preloaded_level_info; // index:episode<index:map<ap_level_info_t>>
static location_types_storage_t preloaded_location_types; // <int doomednum>
static location_table_storage_t preloaded_location_table; // <int episode, <int map, <int index, int64_t ap_id>>>
static item_table_storage_t preloaded_item_table; // <int64_t ap_id, ap_item_t>
static type_sprites_storage_t preloaded_type_sprites; // <int doomednum, std::string sprite_lump_name>
static rename_lumps_storage_t lump_remap_list; // <std::string file, std::vector<remap_entry_t>>
static obituary_storage_t obituary_list; // std::vector<obituary_t>
static energylink_shop_storage_t energylink_shop_items; // std::vector<int>

// All valid level index structures, for easier iteration.
static std::vector<ap_level_index_t> idx_all_levels;
static std::vector<ap_level_index_t> idx_avail_levels;

// ----------------------------------------------------------------------------

Json::Value open_defs(const char *defs_file)
{
	APZipReader *world = APZipReader_FetchFromCache(":world:");
	APZipFile *f = APZipReader_GetFile(world, defs_file);
	if (!f)
	{
		printf("Definitions file '%s' is missing...\n", defs_file);
		return Json::nullValue;
	}

	Json::Value json = AP_ReadJson(f->data, f->size);
	if (json.isNull())
		printf("Failed to initialize game definitions\n");
	return json;
}

// Returns positive on successful load, 0 for failure.
int ap_preload_defs_for_game(const char *game_name)
{
	ap_world_info = ap_get_world(game_name);
	if (!ap_world_info)
	{
		printf("APDOOM: No valid apworld for the game '%s' exists.\n    Currently available games are:\n", game_name);
		const ap_worldinfo_t **games_list = ap_list_worlds();
		for (int i = 0; games_list[i]; ++i)
			printf("    - '%s' -> %s\n", games_list[i]->shortname, games_list[i]->fullname);
		return 0;
	}

	if (!ap_load_world(ap_world_info->shortname))
		return 0;

	Json::Value defs_json = open_defs(ap_world_info->definitions);
	if (defs_json.isNull())
		return 0;

	{ // Recognize supported IWADs, and set up game info for them automatically.
		std::string iwad_name = std::string(ap_world_info->iwad);
		if (iwad_name == "HERETIC.WAD")
			ap_base_game = ap_game_t::heretic;
		else if (iwad_name == "DOOM.WAD" || iwad_name == "CHEX.WAD")
			ap_base_game = ap_game_t::doom;
		else // All others are Doom 2 based, I think?
			ap_base_game = ap_game_t::doom2;
	}

	ap_game_info.ammo_types = new ap_ammo_info_t[ap_episode_count * max_map_count];

	if (!json_parse_location_types(defs_json["ap_location_types"], preloaded_location_types)
		|| !json_parse_type_sprites(defs_json["type_sprites"], preloaded_type_sprites)
		|| !json_parse_item_table(defs_json["item_table"], preloaded_item_table)
		|| !json_parse_location_table(defs_json["location_table"], preloaded_location_table)
		|| !json_parse_level_info(defs_json["level_info"], preloaded_level_info)
		|| !json_parse_map_tweaks(defs_json["map_tweaks"], map_tweak_list)
		|| !json_parse_level_select(defs_json["level_select"], level_select_screens)
		|| !json_parse_rename_lumps(defs_json["rename_lumps"], lump_remap_list)
		|| !json_parse_energylink_shop(defs_json["energy_link_shop"], energylink_shop_items)
		|| !json_parse_game_info(defs_json["game_info"], ap_game_info)
		|| !json_parse_obituaries(defs_json["game_info"]["obituaries"], obituary_list)
	)
	{
		printf("APDOOM: Errors occurred while loading \"%s\".\n", game_name);
		return 0;
	}

	// Make list of ap_level_index_t structures for easier iteration
	for (int ep = 0; ep < (int)preloaded_level_info.size(); ++ep)
		for (int map = 0; map < (int)preloaded_level_info[ep].size(); ++map)
			idx_all_levels.emplace_back(ap_level_index_t{ep, map});
	idx_all_levels.emplace_back(ap_level_index_t{-1, -1});

	return 1;
} 

// ----------------------------------------------------------------------------

const ap_worldinfo_t *ap_loaded_world_info(void)
{
	return ap_world_info;
}

int ap_is_location_type(int doom_type)
{
	return (int)preloaded_location_types.count(doom_type);
}

ap_levelselect_t *ap_get_level_select_info(unsigned int ep)
{
	if (ep >= level_select_screens.size())
		return NULL;
	return &level_select_screens[ep];
}

ap_level_index_t *ap_get_all_levels(void)
{
	return idx_all_levels.data();
}

ap_level_index_t *ap_get_available_levels(void)
{
	return idx_avail_levels.data();
}

// ----------------------------------------------------------------------------

// These are used to do iteration with ap_get_map_tweaks
static ap_level_index_t gmt_level;
static allowed_tweaks_t gmt_type_mask;
static unsigned int gmt_i;

void ap_init_map_tweaks(ap_level_index_t idx, allowed_tweaks_t type_mask)
{
	gmt_i = 0;
	gmt_level.ep = idx.ep;
	gmt_level.map = idx.map;
	gmt_type_mask = type_mask;
}

ap_maptweak_t *ap_get_map_tweaks()
{
	// If map isn't present (has no tweaks), do nothing.
	if (map_tweak_list.count(gmt_level.ep) == 0
		|| map_tweak_list[gmt_level.ep].count(gmt_level.map) == 0)
	{
		return NULL;		
	}

	std::vector<ap_maptweak_t> &tweak_list = map_tweak_list[gmt_level.ep][gmt_level.map];
	while (gmt_i < tweak_list.size())
	{
		ap_maptweak_t *tweak = &tweak_list[gmt_i++];
		if ((tweak->type & TWEAK_TYPE_MASK) != gmt_type_mask)
			continue;
		return tweak;
	}
	return NULL;
}

// ----------------------------------------------------------------------------
// Keeping these getter functions around makes management easier.

static std::vector<std::vector<ap_level_info_t>>& get_level_info_table()
{
	return preloaded_level_info;
}

#include "practice_items.h"

static const std::map<int64_t, ap_item_t>& get_item_type_table()
{
	return preloaded_item_table;
}

static bool get_compat_item(int64_t item_id, ap_item_t& item)
{
	switch (item_id)
	{
	case 359901: // DOOM 1993 Small Health Upgrade
	case 369901: // DOOM II Small Health Upgrade
	case 389901: // DOOM II Scythe Small Health Upgrade
		item = {2014, -1, -1, "Small Health Upgrade"};
		return true;
	case 359902: // DOOM 1993 Small Armor Upgrade
	case 369902: // DOOM II Small Armor Upgrade
	case 389902: // DOOM II Scythe Small Armor Upgrade
		item = {2015, -1, -1, "Small Armor Upgrade"};
		return true;
	case 359903: // DOOM 1993 Health Upgrade
	case 369903: // DOOM II Health Upgrade
	case 389903: // DOOM II Scythe Health Upgrade
		item = {2013, -1, -1, "Health Upgrade"};
		return true;
	case 359904: // DOOM 1993 Armor Upgrade
	case 369904: // DOOM II Armor Upgrade
	case 389904: // DOOM II Scythe Armor Upgrade
		item = {2018, -1, -1, "Armor Upgrade"};
		return true;
	case 359905: // DOOM 1993 Big Armor Upgrade
	case 369905: // DOOM II Big Armor Upgrade
	case 389905: // DOOM II Scythe Big Armor Upgrade
		item = {2019, -1, -1, "Big Armor Upgrade"};
		return true;
	case 369906: // DOOM II Mega Life Upgrade
	case 389906: // DOOM II Scythe Mega Life Upgrade
		item = {83, -1, -1, "Mega Life Upgrade"};
		return true;
	case 359906: // DOOM 1993 Large Health Upgrade
	case 369907: // DOOM II Large Health Upgrade
	case 389907: // DOOM II Scythe Large Health Upgrade
		item = {2022, -1, -1, "Large Health Upgrade"};
		return true;
	default:
		return false;
	}
}

static bool get_ap_item(int64_t item_id, ap_item_t& item)
{
	const auto& item_type_table = get_item_type_table();
	auto it = item_type_table.find(item_id);
	if (it != item_type_table.end())
	{
		item = it->second;
		return true;
	}

	return get_compat_item(item_id, item);
}

static const std::map<int /* ep */, std::map<int /* map */, std::map<int /* index */, int64_t /* loc id */>>>& get_location_table()
{
	return preloaded_location_table;
}

const char* get_weapon_name(int weapon)
{
	return (weapon >= 0 && weapon < ap_weapon_count) ? ap_game_info.weapons[weapon].name : "UNKNOWN";
}

const char* get_ammo_name(int weapon)
{
	return (weapon >= 0 && weapon < ap_ammo_count) ? ap_game_info.ammo_types[weapon].name : "UNKNOWN";
}

const ap_item_t* ap_get_item(int item_id)
{
	const auto& item_it = preloaded_item_table.find(item_id);
	return (item_it != preloaded_item_table.end() ? &item_it->second : NULL);
}

const char* ap_get_sprite(int doom_type)
{
	if (ap_state.enhanced_ap_powerups)
	{
		const char* enhanced_sprite = NULL;
		switch (doom_type)
		{
		case 2018: enhanced_sprite = "APARA0"; break;
		case 2019: enhanced_sprite = "APMAA0"; break;
		case 2013: enhanced_sprite = "APSCA0"; break;
		case 83: enhanced_sprite = "APMSA0"; break;
		case 2022: enhanced_sprite = "APINA0"; break;
		case 2024: enhanced_sprite = "APISA0"; break;
		case 2023: enhanced_sprite = "APBEA0"; break;
		}
		if (enhanced_sprite && W_CheckNumForName(enhanced_sprite) >= 0)
		{
			return enhanced_sprite;
		}
	}

	const auto& sprite_it = preloaded_type_sprites.find(doom_type);
	if (sprite_it == preloaded_type_sprites.end())
	{
		return NULL;
	}

	const char* sprite = sprite_it->second.c_str();
	if (W_CheckNumForName(sprite) >= 0)
	{
		return sprite;
	}

	if (doom_type == 2014)
	{
		return "BON1A0";
	}
	if (doom_type == 2015)
	{
		return "BON2A0";
	}

	return NULL;
}

// ============================================================================

static int get_original_music_for_level(ap_level_index_t& idx)
{
	switch (ap_base_game)
	{
		case ap_game_t::doom:
		{
			// Correcting Episode 4's music is no longer needed
			return 1 + (idx.ep) * 9 + (idx.map);
		}
		case ap_game_t::doom2:
		{
			int d2map = ap_index_to_map(idx);
			// Hack for NRFTL, should really give this a proper fix at some point
			if (d2map > 40) return 97 + (d2map - 40) - 1;
			return 62 + d2map - 1;
		}
		case ap_game_t::heretic:
		{
			return (idx.ep) * 9 + (idx.map);
		}
	}

	// For now for doom and heretic
	return -1;
}


int ap_get_map_count(int ep)
{
	--ep;
	auto& level_info_table = get_level_info_table();
	if (ep < 0 || ep >= (int)level_info_table.size()) return -1;
	return (int)level_info_table[ep].size();
}


int ap_total_check_count(const ap_level_info_t *level_info)
{
	return level_info->true_check_count;
}


int ap_is_location_checked(ap_level_index_t idx, int index)
{
	auto level_state = ap_get_level_state(idx);
	for (int i = 0; i < level_state->check_count; ++i)
	{
		if (level_state->checks[i] == index) return true;
	}
	return false;
}


static int collect_level_weapon_flags(ap_level_index_t idx, bool *available)
{
	memset(available, 0, sizeof(bool) * ap_weapon_count);

	ap_level_info_t *level_info = ap_get_level_info(idx);
	if (!level_info)
		return 0;

	const auto& weapons_map = get_weapons_map();
	available[1] = true; // Starting pistol.
	int total = 1;
	for (int i = 0; i < level_info->thing_count; ++i)
	{
		if (level_info->thing_infos[i].location_id <= 0)
			continue;
		if (suppressed_locations.count(level_info->thing_infos[i].location_id))
			continue;

		auto weapon_it = weapons_map.find(level_info->thing_infos[i].doom_type);
		if (weapon_it == weapons_map.end())
			continue;

		const int weapon = weapon_it->second;
		if (weapon < 1 || weapon >= ap_weapon_count)
			continue;
		if (ap_state.remove_chainsaws_from_game && weapon == 7)
			continue;
		if (available[weapon])
			continue;

		available[weapon] = true;
		++total;
	}

	return total;
}


static int progressive_weapon_sequence(int *weapons, int weapon_count)
{
	static const int doom_progression[] = {1, 2, 3, 7, 4, 5, 6};
	static const int doom2_progression[] = {1, 2, 3, 7, 8, 4, 5, 6};

	const int *progression = doom_progression;
	int progression_count = sizeof(doom_progression) / sizeof(doom_progression[0]);
	if (ap_base_game == ap_game_t::doom2)
	{
		progression = doom2_progression;
		progression_count = sizeof(doom2_progression) / sizeof(doom2_progression[0]);
	}

	int count = 0;
	for (int i = 0; i < progression_count && count < weapon_count; ++i)
	{
		if (ap_state.remove_chainsaws_from_game && progression[i] == 7)
			continue;
		weapons[count++] = progression[i];
	}
	return count > 0 ? count : 1;
}


static int get_doom_type_for_weapon(int weapon)
{
	const auto& weapons_map = get_weapons_map();
	for (const auto& weapon_it : weapons_map)
		if (weapon_it.second == weapon)
			return weapon_it.first;
	return 0;
}


static bool is_logic_weapon_for_level(ap_level_index_t idx, int doom_type)
{
	if (global_logic_weapon_doom_types.count(doom_type))
		return true;

	ap_level_info_t *level_info = ap_get_level_info(idx);
	if (!level_info)
		return false;

	const std::string ap_key = std::to_string(idx.ep + 1) + ":" + std::to_string(idx.map + 1);
	const auto ap_level_it = level_logic_weapon_doom_types.find(ap_key);
	if (ap_level_it != level_logic_weapon_doom_types.end()
		&& ap_level_it->second.count(doom_type))
		return true;

	const std::string game_key = std::to_string(level_info->game_episode) + ":" + std::to_string(level_info->game_map);
	const auto game_level_it = level_logic_weapon_doom_types.find(game_key);
	return game_level_it != level_logic_weapon_doom_types.end()
		&& game_level_it->second.count(doom_type);
}


static int progressive_level_weapon_step_count(ap_level_index_t idx)
{
	int progression[8];
	bool available[32];
	if (ap_weapon_count <= 0 || ap_weapon_count > (int)(sizeof(available) / sizeof(available[0])))
		return 0;

	const int progression_count = progressive_weapon_sequence(progression, sizeof(progression) / sizeof(progression[0]));
	collect_level_weapon_flags(idx, available);

	int highest_step = -1;
	for (int i = 0; i < progression_count; ++i)
	{
		const int weapon = progression[i];
		if (weapon >= 0 && weapon < ap_weapon_count && available[weapon])
			highest_step = i;
	}

	return highest_step;
}


int ap_level_weapon_count(ap_level_index_t idx)
{
	bool available[32];
	if (ap_weapon_count <= 0 || ap_weapon_count > (int)(sizeof(available) / sizeof(available[0])))
		return 0;

	if (ap_state.progressive_weapons)
	{
		int progression[8];
		const int progression_count = progressive_weapon_sequence(progression, sizeof(progression) / sizeof(progression[0]));
		const int total = progressive_level_weapon_step_count(idx);
		ap_level_state_t *level_state = ap_get_level_state(idx);

		int count = 0;
		for (int i = 1; i <= total && i < progression_count; ++i)
		{
			const int weapon = progression[i];
			if (weapon >= 0 && weapon < ap_weapon_count && level_state->weapon_owned[weapon])
				++count;
		}
		return count;
	}

	collect_level_weapon_flags(idx, available);

	ap_level_state_t *level_state = ap_get_level_state(idx);
	const int *weapon_owned = (ap_state.level_specific_weapons || ap_state.progressive_weapons)
		? level_state->weapon_owned
		: ap_state.player_state.weapon_owned;

	int count = 0;
	for (int i = 0; i < ap_weapon_count; ++i)
	{
		if (i != 1 && available[i] && weapon_owned[i])
			++count;
	}
	return count;
}


int ap_total_level_weapon_count(ap_level_index_t idx)
{
	bool available[32];
	if (ap_weapon_count <= 0 || ap_weapon_count > (int)(sizeof(available) / sizeof(available[0])))
		return 0;

	if (ap_state.progressive_weapons)
		return progressive_level_weapon_step_count(idx);

	const int total = collect_level_weapon_flags(idx, available);
	return total > 0 ? total - 1 : 0;
}


int ap_get_level_weapon_display(ap_level_index_t idx, ap_level_weapon_display_t* weapons, int max_weapons)
{
	bool available[32];
	int progression[8];
	bool added[32];

	if (!weapons || max_weapons <= 0)
		return 0;
	if (ap_weapon_count <= 0 || ap_weapon_count > (int)(sizeof(available) / sizeof(available[0])))
		return 0;

	memset(added, 0, sizeof(added));
	collect_level_weapon_flags(idx, available);

	ap_level_state_t *level_state = ap_get_level_state(idx);
	const int *weapon_owned = (ap_state.level_specific_weapons || ap_state.progressive_weapons)
		? (level_state ? level_state->weapon_owned : NULL)
		: ap_state.player_state.weapon_owned;

	int count = 0;
	const int progression_count = progressive_weapon_sequence(progression, sizeof(progression) / sizeof(progression[0]));
	for (int pass = 0; pass < 2 && count < max_weapons; ++pass)
	{
		const int pass_count = pass == 0 ? progression_count : ap_weapon_count;
		for (int i = 0; i < pass_count && count < max_weapons; ++i)
		{
			const int weapon = pass == 0 ? progression[i] : i;
			if (weapon <= 1 || weapon >= ap_weapon_count)
				continue;
			if (!available[weapon] || added[weapon])
				continue;

			const int doom_type = get_doom_type_for_weapon(weapon);
			const char *sprite = doom_type ? ap_get_sprite(doom_type) : NULL;
			if (!sprite)
				continue;

			weapons[count].weapon = weapon;
			weapons[count].doom_type = doom_type;
			weapons[count].owned = weapon_owned && weapon_owned[weapon];
			weapons[count].logic = is_logic_weapon_for_level(idx, doom_type);
			weapons[count].sprite = sprite;
			added[weapon] = true;
			++count;
		}
	}

	return count;
}


ap_level_info_t* ap_get_level_info(ap_level_index_t idx)
{
	auto& level_info_table = get_level_info_table();
	if (idx.ep < 0 || idx.ep >= (int)level_info_table.size()) return nullptr;
	if (idx.map < 0 || idx.map >= (int)level_info_table[idx.ep].size()) return nullptr;
	return &level_info_table[idx.ep][idx.map];
}


ap_level_state_t* ap_get_level_state(ap_level_index_t idx)
{
	return &ap_state.level_states[idx.ep * max_map_count + idx.map];
}


std::string string_to_hex(const char* str)
{
    static const char hex_digits[] = "0123456789ABCDEF";

	std::string out;
	std::string in = str;

    out.reserve(in.length() * 2);
    for (unsigned char c : in)
    {
        out.push_back(hex_digits[c >> 4]);
        out.push_back(hex_digits[c & 15]);
    }

    return out;
}


void recalc_max_ammo()
{
	static const int enhanced_doom_ammo_add[4] = {5, 2, 20, 2};
	for (int i = 0; i < ap_ammo_count; ++i)
	{
		const bool use_enhanced_doom_ammo =
			ap_state.enhanced_ammo
			&& (ap_base_game == ap_game_t::doom || ap_base_game == ap_game_t::doom2)
			&& i < (int)(sizeof(enhanced_doom_ammo_add) / sizeof(enhanced_doom_ammo_add[0]));
		int gained_max = use_enhanced_doom_ammo
			? enhanced_doom_ammo_add[i] * ap_state.player_state.capacity_upgrades[i]
			: ap_state.max_ammo_add[i] * ap_state.player_state.capacity_upgrades[i];
		const int recalc_max = ap_state.max_ammo_start[i] + gained_max;

		ap_state.player_state.max_ammo[i] = (recalc_max > 999) ? 999 : recalc_max;
	}
}

int validate_doom_location(ap_level_index_t idx, int index)
{
    ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info) return 0;
	if (index < 0) return 1;
    if (index >= level_info->thing_count) return 0;
	if (level_info->thing_infos[index].location_id <= 0) return 0;
	if (suppressed_locations.count(level_info->thing_infos[index].location_id)) return 0;
    return 1;
}

int ap_is_location_suppressed(ap_level_index_t idx, int index)
{
    ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info) return 0;
    if (index < 0 || index >= level_info->thing_count) return 0;
	if (level_info->thing_infos[index].location_id <= 0) return 0;
	return (int)suppressed_locations.count(level_info->thing_infos[index].location_id);
}

static void make_init_file(const char *contents)
{
	if (!ap_settings.temp_init_file)
		return;
	std::ofstream file(ap_settings.temp_init_file, std::ios::binary | std::ios::trunc);
	if (file.is_open())
		file << contents << '\n';
}


int apdoom_init(ap_settings_t* settings)
{
	printf("%s\n", APDOOM_VERSION_FULL_TEXT);

#if 0 // If there's an invalid memory access being done here, I want to find it.
	ap_notification_icons.reserve(4096); // 1MB. A bit exessive, but I got a crash with invalid strings and I cannot figure out why. Let's not take any chances...
#endif
	memset(&ap_state, 0, sizeof(ap_state));

	settings->game = ap_world_info->apname;
	if (ap_base_game == ap_game_t::heretic)
	{
		ap_weapon_count = 9;
		ap_ammo_count = 6;
		ap_powerup_count = 9;
		ap_inventory_count = 14;
	}
	else // Doom or Doom 2, both use the same variables here
	{
		ap_weapon_count = 9;
		ap_ammo_count = 4;
		ap_powerup_count = 6;
		ap_inventory_count = 0;
	}

	const auto& level_info_table = get_level_info_table();
	ap_episode_count = (int)level_info_table.size();
	max_map_count = 0; // That's really the map count
	for (const auto& episode_level_info : level_info_table)
	{
		max_map_count = std::max(max_map_count, (int)episode_level_info.size());
	}

	ap_state.level_states = new ap_level_state_t[ap_episode_count * max_map_count];
	ap_state.episodes = new int[ap_episode_count];
	ap_state.player_state.powers = new int[ap_powerup_count];
	ap_state.player_state.weapon_owned = new int[ap_weapon_count];
	ap_state.player_state.ammo = new int[ap_ammo_count];
	ap_state.player_state.max_ammo = new int[ap_ammo_count];
	ap_state.player_state.inventory = ap_inventory_count ? new ap_inventory_slot_t[ap_inventory_count] : nullptr;

	memset(ap_state.level_states, 0, sizeof(ap_level_state_t) * ap_episode_count * max_map_count);
	memset(ap_state.episodes, 0, sizeof(int) * ap_episode_count);
	memset(ap_state.player_state.powers, 0, sizeof(int) * ap_powerup_count);
	memset(ap_state.player_state.weapon_owned, 0, sizeof(int) * ap_weapon_count);
	memset(ap_state.player_state.ammo, 0, sizeof(int) * ap_ammo_count);
	memset(ap_state.player_state.max_ammo, 0, sizeof(int) * ap_ammo_count);
	if (ap_inventory_count)
		memset(ap_state.player_state.inventory, 0, sizeof(ap_inventory_slot_t) * ap_inventory_count);

	ap_state.player_state.health = ap_game_info.start_health;
	ap_state.player_state.max_health = 200;
	ap_state.player_state.armor_points = ap_game_info.start_armor;
	ap_state.player_state.max_armor = 200;
	ap_state.player_state.armor_type = 1;
	ap_state.small_health_upgrade = 10;
	ap_state.small_armor_upgrade = 10;
	ap_state.health_upgrade = 5;
	ap_state.large_health_upgrade = 10;
	ap_state.armor_upgrade = 5;
	ap_state.large_armor_upgrade = 10;
	ap_state.mega_upgrade = 10;

	ap_state.player_state.ready_weapon = 1;
	ap_state.player_state.weapon_owned[0] = 1; // Fist
	ap_state.player_state.weapon_owned[1] = 1; // Pistol
	ap_state.player_state.ammo[0] = ap_game_info.weapons[1].start_ammo; // Clip

	// Ammo capacity management
	ap_state.max_ammo_start = new int[ap_ammo_count];
	ap_state.max_ammo_add = new int[ap_ammo_count];
	ap_state.max_ammo_steps = new int[ap_ammo_count];
	ap_state.player_state.capacity_upgrades = new int[ap_ammo_count];

	// default to regular max ammos for games without custom max ammo set
	for (int i = 0; i < ap_ammo_count; ++i)
	{
		ap_state.max_ammo_start[i] = ap_game_info.ammo_types[i].max_ammo;
		ap_state.max_ammo_add[i] = ap_game_info.ammo_types[i].max_ammo;
		ap_state.max_ammo_steps[i] = 1;
	}
	memset(ap_state.player_state.capacity_upgrades, 0, sizeof(int) * ap_ammo_count);

	// initialize checked location lists
	for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
	{
		auto level_state = ap_get_level_state(*idx);

		level_state->max_check_count = ap_get_level_info(*idx)->check_count + 1;
		level_state->checks = new int64_t[level_state->max_check_count];
		level_state->weapon_owned = new int[ap_weapon_count];
		for (int k = 0; k < level_state->max_check_count; ++k)
			level_state->checks[k] = -1;
		memset(level_state->weapon_owned, 0, sizeof(int) * ap_weapon_count);
	}

	ap_settings = *settings;

	if (ap_settings.override_skill)
		ap_state.difficulty = ap_settings.skill;
	if (ap_settings.override_monster_rando)
		ap_state.random_monsters = ap_settings.monster_rando;
	if (ap_settings.override_item_rando)
		ap_state.random_items = ap_settings.item_rando;
	if (ap_settings.override_music_rando)
		ap_state.random_music = ap_settings.music_rando;
	if (ap_settings.override_flip_levels)
		ap_state.flip_levels = ap_settings.flip_levels;
	if (ap_settings.override_reset_level_on_death)
		ap_state.reset_level_on_death = ap_settings.reset_level_on_death;

	if (ap_practice_mode) // Practice game, no connection
	{
		printf("APDOOM: Initializing Game: \"%s\", Practice Mode\n", settings->game);

		// Select all episodes.
		for (int ep = 0; ep < ap_episode_count; ++ep)
			ap_state.episodes[ep] = 1;

		ap_settings.player_name = "Player";
		recalc_max_ammo();

		srand((unsigned int)time(NULL));
		ap_seed_string = "practmp_" + std::to_string(rand());
		ap_save_path = std::filesystem::current_path() / ap_seed_string;
		std::filesystem::create_directories(ap_save_path);
	}
	else
	{

	printf("APDOOM: Initializing Game: \"%s\", Server: %s, Slot: %s\n", settings->game, settings->ip, settings->player_name);
	AP_NetworkVersion version = {0, 6, 3};
	AP_SetClientVersion(&version);
    AP_Init(ap_settings.ip, ap_settings.game, ap_settings.player_name, ap_settings.passwd);
	AP_SetDeathLinkSupported(ap_settings.force_deathlink_off ? false : true);
	AP_SetDeathLinkRecvCallback(f_deathlink_apcpp);
	AP_SetItemClearCallback(f_itemclr);
	AP_SetItemRecvCallback(f_itemrecv_apcpp);
	AP_SetLocationCheckedCallback(f_locrecv);
	AP_SetLocationInfoCallback(f_locinfo);
	AP_RegisterSlotDataRawCallback("goal", f_goal);
	AP_RegisterSlotDataIntCallback("difficulty", f_difficulty);
	AP_RegisterSlotDataIntCallback("reset_level_on_death", f_reset_level_on_death);
	AP_RegisterSlotDataIntCallback("require_100_percent_kills_to_exit", f_require_100_percent_kills_to_exit);
	AP_RegisterSlotDataIntCallback("keep_completed_maps_cleared", f_keep_completed_maps_cleared);
	AP_RegisterSlotDataIntCallback("show_monsters_on_automap", f_show_monsters_on_automap);
	AP_RegisterSlotDataIntCallback("random_monsters", f_random_monsters);
	AP_RegisterSlotDataIntCallback("random_pickups", f_random_items);
	AP_RegisterSlotDataIntCallback("random_music", f_random_music);
	AP_RegisterSlotDataIntCallback("pistol_start", f_pistol_start);
	AP_RegisterSlotDataIntCallback("resume_pistol_start_levels", f_resume_pistol_start_levels);
	AP_RegisterSlotDataIntCallback("vanilla_powerup_locations", f_vanilla_powerup_locations);
	AP_RegisterSlotDataIntCallback("enhanced_ap_bonus_items", f_enhanced_ap_bonus_items);
	AP_RegisterSlotDataIntCallback("enhanced_ap_powerups", f_enhanced_ap_powerups);
	AP_RegisterSlotDataIntCallback("enhanced_ammo", f_enhanced_ammo);
	AP_RegisterSlotDataIntCallback("remove_chainsaws_from_game", f_remove_chainsaws_from_game);
	AP_RegisterSlotDataIntCallback("level_specific_weapons", f_level_specific_weapons);
	AP_RegisterSlotDataIntCallback("progressive_weapons", f_progressive_weapons);
	AP_RegisterSlotDataIntCallback("global_progressive_weapons", f_global_progressive_weapons);
	AP_RegisterSlotDataIntCallback("vanilla_keys_mode", f_vanilla_keys_mode);
	AP_RegisterSlotDataIntCallback("keys_give_map_access", f_keys_give_map_access);
	AP_RegisterSlotDataIntCallback("release_keys_for_completed_levels", f_release_keys_for_completed_levels);
	AP_RegisterSlotDataIntCallback("release_computer_area_map_for_completed_levels", f_release_computer_area_map_for_completed_levels);
	AP_RegisterSlotDataIntCallback("release_weapons_for_completed_levels", f_release_weapons_for_completed_levels);
	AP_RegisterSlotDataRawCallback("suppressed_locations", f_suppressed_locations);
	AP_RegisterSlotDataRawCallback("logic_weapon_types", f_logic_weapon_types);
	AP_RegisterSlotDataRawCallback("vanilla_key_locations", f_vanilla_key_locations);
	AP_RegisterSlotDataRawCallback("vanilla_weapon_locations", f_vanilla_weapon_locations);
	AP_RegisterSlotDataRawCallback("randomized_pickup_locations", f_randomized_pickup_locations);
	AP_RegisterSlotDataRawCallback("enhanced_bonus_locations", f_enhanced_bonus_locations);
	AP_RegisterSlotDataRawCallback("enhanced_powerup_locations", f_enhanced_powerup_locations);
	AP_RegisterSlotDataRawCallback("enhanced_ammo_locations", f_enhanced_ammo_locations);
	AP_RegisterSlotDataRawCallback("episodes", f_episodes);
	AP_RegisterSlotDataIntCallback("episode1", f_episode1);
	AP_RegisterSlotDataIntCallback("episode2", f_episode2);
	AP_RegisterSlotDataIntCallback("episode3", f_episode3);
	AP_RegisterSlotDataIntCallback("episode4", f_episode4);
	AP_RegisterSlotDataRawCallback("ammo_start", f_ammo_start);
	AP_RegisterSlotDataRawCallback("ammo_add", f_ammo_add);
	AP_RegisterSlotDataRawCallback("ammo_steps", f_ammo_steps);
	AP_RegisterSlotDataIntCallback("small_health_upgrade", f_small_health_upgrade);
	AP_RegisterSlotDataIntCallback("small_armor_upgrade", f_small_armor_upgrade);
	AP_RegisterSlotDataIntCallback("health_upgrade", f_health_upgrade);
	AP_RegisterSlotDataIntCallback("large_health_upgrade", f_large_health_upgrade);
	AP_RegisterSlotDataIntCallback("armor_upgrade", f_armor_upgrade);
	AP_RegisterSlotDataIntCallback("large_armor_upgrade", f_large_armor_upgrade);
	AP_RegisterSlotDataIntCallback("mega_upgrade", f_mega_upgrade);
	AP_RegisterSlotDataRawCallback("energy_link", f_energylink);
	if (ap_base_game != ap_game_t::heretic)
		AP_RegisterSlotDataIntCallback("flip_levels", f_flip_levels);
	suppressed_locations_received = false;
	global_logic_weapon_doom_types.clear();
	level_logic_weapon_doom_types.clear();
    AP_Start();

	// Block DOOM until connection succeeded or failed
	auto start_time = std::chrono::steady_clock::now();
	while (true)
	{
		bool should_break = false;
		switch (AP_GetConnectionStatus())
		{
			case AP_ConnectionStatus::Connected:
				// Connected, but not authenticated. Likely fetching datapackages.
				break;
			case AP_ConnectionStatus::Authenticated:
			{
				if (detected_old_apworld)
				{
					printf("APDOOM: Older versions of the APWorld are not supported.\n");
					printf("  Please use APDOOM 1.2.0 to connect to this slot.\n");
					make_init_file("OldWorldVersion");
					return 0;
				}

				printf("APDOOM: Authenticated\n");
				AP_GetRoomInfo(&ap_room_info);

				printf("APDOOM: Room Info:\n");
				printf("  Network Version: %i.%i.%i\n", ap_room_info.version.major, ap_room_info.version.minor, ap_room_info.version.build);
				printf("  Tags:\n");
				for (const auto& tag : ap_room_info.tags)
					printf("    %s\n", tag.c_str());
				printf("  Password required: %s\n", ap_room_info.password_required ? "true" : "false");
				printf("  Permissions:\n");
				for (const auto& permission : ap_room_info.permissions)
					printf("    %s = %i:\n", permission.first.c_str(), permission.second);
				printf("  Hint cost: %i\n", ap_room_info.hint_cost);
				printf("  Location check points: %i\n", ap_room_info.location_check_points);
				printf("  Data package checksums:\n");
				for (const auto& kv : ap_room_info.datapackage_checksums)
					printf("    %s = %s:\n", kv.first.c_str(), kv.second.c_str());
				printf("  Seed name: %s\n", ap_room_info.seed_name.c_str());
				printf("  Time: %f\n", ap_room_info.time);

				ap_was_connected = true;
				if (ap_settings.save_dir != NULL)
					ap_save_path = ap_settings.save_dir;
				else
					ap_save_path = std::filesystem::current_path() / "save";

				ap_seed_string = "AP_" + ap_room_info.seed_name + "_" + string_to_hex(ap_settings.player_name);
				ap_save_path /= ap_world_info->shortname;
				ap_save_path /= ap_seed_string;

				// Create a directory where saves will go for this AP seed.
#ifdef _WIN32
				printf("APDOOM: Save directory: %ws\n", ap_save_path.c_str());
#else
				printf("APDOOM: Save directory: %s\n", ap_save_path.c_str());
#endif
				std::filesystem::create_directories(ap_save_path);

				// Make sure that ammo starts at correct base values no matter what
				recalc_max_ammo();

				initial_timestamp = (long int)time(NULL);
				load_state();

				// Ask the server for race mode state.
				race_mode_request.key = "_read_race_mode";
				race_mode_request.type = AP_DataType::Int;
				race_mode_request.value = &ap_race_mode;
				AP_GetServerData(&race_mode_request);

				should_break = true;
				break;
			}
			case AP_ConnectionStatus::ConnectionRefused:
				printf("APDOOM: Server refused connection. Check your player name, password, game, and server version.\n");
				make_init_file("ConnectFailed");
				return 0;
			default:
				// Not connected yet, check for timeout.
				if (std::chrono::steady_clock::now() - start_time > std::chrono::seconds(10))
				{
					printf("APDOOM: Failed to connect to server (timeout 10s), check your connection settings.\n");
					make_init_file("ConnectFailed");
					return 0;
				}
				break;
		}
		if (should_break) break;
		std::this_thread::sleep_for(std::chrono::milliseconds(100));
	}

	} // if (ap_practice_mode)

	if (!ap_practice_mode)
	{
		auto start_time = std::chrono::steady_clock::now();
		while (!suppressed_locations_received.load())
		{
			std::this_thread::sleep_for(std::chrono::milliseconds(10));
			if (std::chrono::steady_clock::now() - start_time > std::chrono::seconds(5))
			{
				printf("APDOOM: Timeout waiting for slot data. Location scouts may include suppressed checks.\n");
				break;
			}
		}
	}

	// If none episode is selected, select the first one.
	int ep_count = 0;
	for (int i = 0; i < ap_episode_count; ++i)
		if (ap_state.episodes[i])
			ep_count++;
	if (!ep_count)
	{
		printf("APDOOM: No episode selected, selecting episode 1\n");
		ap_state.episodes[0] = 1;
	}

	// We can now set the available level list properly, because we now know it.
	for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
	{
		if (ap_state.episodes[idx->ep])
			idx_avail_levels.push_back(*idx);
	}
	idx_avail_levels.emplace_back(ap_level_index_t{-1, -1});

	// Enable all maps by default in practice mode
	if (ap_practice_mode)
	{
		for (ap_level_index_t *idx = ap_get_available_levels(); idx->ep != -1; ++idx)
		{
			auto level_state = ap_get_level_state(*idx);
			level_state->unlocked = 1;
			level_state->has_map = 1;
		}
	}

	// Set up true check counts now
	for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
	{
		auto level_info = ap_get_level_info(*idx);
		level_info->true_check_count = level_info->check_count;

		for (int k = 0; k < level_info->thing_count; ++k)
		{
			if (suppressed_locations.count(level_info->thing_infos[k].location_id))
				--level_info->true_check_count;
		}
	}

	// Randomly flip levels based on the seed
	if (ap_state.flip_levels == 1)
	{
		printf("APDOOM: All levels flipped\n");
		for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
			ap_get_level_state(*idx)->flipped = 1;
	}
	else if (ap_state.flip_levels == 2)
	{
		printf("APDOOM: Levels randomly flipped\n");
		ap_srand(31337);
		for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
			ap_get_level_state(*idx)->flipped = ap_rand() % 2;
	}

	{ // Music Shuffle
		std::vector<int> music_pool;
		int track = 0;

		// Map original music to every level to start
		for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
			ap_get_level_state(*idx)->music = get_original_music_for_level(*idx);

		switch (ap_state.random_music)
		{
		case 1: // Shuffle selected only
			for (ap_level_index_t *idx = ap_get_available_levels(); idx->ep != -1; ++idx)
				music_pool.push_back(ap_get_level_state(*idx)->music);
			goto post_init_music;
		case 2: // Shuffle all music
			for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
				music_pool.push_back(ap_get_level_state(*idx)->music);
			goto post_init_music;
		default:
			break;

		post_init_music:
			ap_srand(67890);
			ap_shuffle(music_pool.data(), music_pool.size());

			printf("APDOOM: Random Music:\n");
			for (ap_level_index_t *idx = ap_get_available_levels(); idx->ep != -1; ++idx)
			{
				const int mus = music_pool[track++];
				ap_get_level_state(*idx)->music = mus;
				printf("  Episode %i Map %i: Music track %i\n", idx->ep, idx->map, mus);
			}
		}
	}

	// Scout locations to see which are progression/useful.
	if (ap_practice_mode)
	{
		// Do no action when offline.
	}
	else
	{
		std::set<int64_t> location_scouts;
		std::set<int64_t> missing_location_scouts;

		const auto& loc_table = get_location_table();
		for (const auto& kv1 : loc_table)
		{
			for (const auto& kv2 : kv1.second)
			{
				// location_table is keyed by AP episode/map, not Doom's in-WAD
				// gameepisode/gamemap. PWADs such as Scythe split MAP11 into
				// AP episode 2/map 1 while the actual game map remains MAP11.
				ap_level_index_t idx = {kv1.first - 1, kv2.first - 1};
				ap_level_info_t* level_info = ap_get_level_info(idx);
				if (!level_info
					|| idx.ep < 0
					|| idx.ep >= ap_episode_count
					|| !ap_state.episodes[idx.ep])
					continue;

				for (const auto& kv3 : kv2.second)
				{
					if (kv3.first == -1) continue;
					if (suppressed_locations.count(kv3.second)) continue;

					if (validate_doom_location(idx, kv3.first))
					{
						location_scouts.insert(kv3.second);
					}
				}
			}
		}

		for (auto loc_id : location_scouts)
		{
			if (!ap_scouted_locations.count(loc_id))
				missing_location_scouts.insert(loc_id);
		}
		
		if (!missing_location_scouts.empty())
		{
			printf("APDOOM: Scouting for %i missing locations (%i total)...\n",
				(int)missing_location_scouts.size(), (int)location_scouts.size());
			AP_SendLocationScouts(std::vector<int64_t>(missing_location_scouts.begin(), missing_location_scouts.end()), 0);

			auto all_scouts_received = [&missing_location_scouts]() {
				for (auto loc_id : missing_location_scouts)
				{
					if (!ap_scouted_locations.count(loc_id))
						return false;
				}
				return true;
			};

			// Wait for all requested location infos, including filler locations.
			auto start_time = std::chrono::steady_clock::now();
			while (!all_scouts_received())
			{
				apdoom_update();
			
				std::this_thread::sleep_for(std::chrono::milliseconds(100));
				if (std::chrono::steady_clock::now() - start_time > std::chrono::seconds(10))
				{
					printf("APDOOM: Timeout waiting for LocationScouts. 10s\n  Do you have a VPN active?\n  Unscouted checks will all look non-progression.");
					break;
				}
			}
		}
		else
		{
			printf("APDOOM: Scout locations cached loaded\n");
		}
	}
	
	printf("APDOOM: Initialized\n");
	ap_initialized = true;
	make_init_file("OK");
	return 1;
}


void apdoom_shutdown()
{
	if (ap_was_connected)
		save_state();
}


void apdoom_save_state()
{
	if (ap_was_connected)
		save_state();
}


static void json_get_int(const Json::Value& json, int& out_or_default)
{
	if (json.isInt())
		out_or_default = json.asInt();
}


static void json_get_bool_or(const Json::Value& json, int& out_or_default)
{
	if (json.isInt())
		out_or_default |= json.asInt();
}


const char* get_power_name(int weapon)
{
	switch (weapon)
	{
		case 0: return "Invulnerability";
		case 1: return "Strength";
		case 2: return "Invisibility";
		case 3: return "Hazard suit";
		case 4: return "Computer area map";
		case 5: return "Infrared";
		default: return "UNKNOWN";
	}
}


void load_state()
{
	printf("APDOOM: Load state\n");

	std::ifstream f(ap_save_path / "apstate.json");
	if (!f.is_open())
	{
		printf("  None found.\n");
		return; // Could be no state yet, that's fine
	}
	Json::Value json;
	try
	{
		f >> json;
	}
	catch (...)
	{
		f.close();
		printf("  Error loading state.\n");
		return;
	}

	f.close();

	initial_timestamp = json.get("launcher_data", Json::objectValue).get("_initial_timestamp", 0).asInt64();
	if (!initial_timestamp)
		initial_timestamp = (long int)time(NULL);

	// Player state
	json_get_int(json["player"]["health"], ap_state.player_state.health);
	if (!ap_items_synced)
	{
		json_get_int(json["player"]["max_health"], ap_state.player_state.max_health);
		json_get_int(json["player"]["max_armor"], ap_state.player_state.max_armor);
		ap_state.max_health_bonus_tenths = (ap_state.player_state.max_health - 200) * 10;
		ap_state.max_armor_bonus_tenths = (ap_state.player_state.max_armor - 200) * 10;
		json_get_int(json["player"]["max_health_bonus_tenths"], ap_state.max_health_bonus_tenths);
		json_get_int(json["player"]["max_armor_bonus_tenths"], ap_state.max_armor_bonus_tenths);
		json_get_int(json["player"]["enhanced_backpack_count"], ap_state.enhanced_backpack_count);
	}
	json_get_int(json["player"]["armor_points"], ap_state.player_state.armor_points);
	json_get_int(json["player"]["armor_type"], ap_state.player_state.armor_type);
	json_get_int(json["player"]["ready_weapon"], ap_state.player_state.ready_weapon);
	json_get_int(json["player"]["kill_count"], ap_state.player_state.kill_count);
	json_get_int(json["player"]["item_count"], ap_state.player_state.item_count);
	json_get_int(json["player"]["secret_count"], ap_state.player_state.secret_count);
	for (int i = 0; i < ap_powerup_count; ++i)
		json_get_int(json["player"]["powers"][i], ap_state.player_state.powers[i]);
	for (int i = 0; i < ap_weapon_count; ++i)
		json_get_bool_or(json["player"]["weapon_owned"][i], ap_state.player_state.weapon_owned[i]);
	for (int i = 0; i < ap_ammo_count; ++i)
	{
		json_get_int(json["player"]["ammo"][i], ap_state.player_state.ammo[i]);

		// Only load max ammos from save if we haven't synced our items yet!
		// Otherwise we may be overwriting more up-to-date data.
		if (!ap_items_synced)
			json_get_int(json["player"]["max_ammo"][i], ap_state.player_state.max_ammo[i]);		
	}
	for (int i = 0; i < ap_inventory_count; ++i)
	{
		const auto& inventory_slot = json["player"]["inventory"][i];
		json_get_int(inventory_slot["type"], ap_state.player_state.inventory[i].type);
		json_get_int(inventory_slot["count"], ap_state.player_state.inventory[i].count);
	}

	printf("  Player State:\n");
	printf("    Health %i:\n", ap_state.player_state.health);
	printf("    Armor points %i:\n", ap_state.player_state.armor_points);
	printf("    Armor type %i:\n", ap_state.player_state.armor_type);
	printf("    Ready weapon: %s\n", get_weapon_name(ap_state.player_state.ready_weapon));
	printf("    Kill count %i:\n", ap_state.player_state.kill_count);
	printf("    Item count %i:\n", ap_state.player_state.item_count);
	printf("    Secret count %i:\n", ap_state.player_state.secret_count);
	printf("    Active powerups:\n");
	for (int i = 0; i < ap_powerup_count; ++i)
		if (ap_state.player_state.powers[i])
			printf("    %s\n", get_power_name(i));
	printf("    Owned weapons:\n");
	for (int i = 0; i < ap_weapon_count; ++i)
		if (ap_state.player_state.weapon_owned[i])
			printf("      %s\n", get_weapon_name(i));
	printf("    Ammo:\n");
	for (int i = 0; i < ap_ammo_count; ++i)
		printf("      %s = %i / %i\n", get_ammo_name(i),
			ap_state.player_state.ammo[i],
			ap_state.player_state.max_ammo[i]);

	// Level states
	ap_state.has_computer_area_map = 0;
	for (int i = 0; i < ap_episode_count; ++i)
	{
		int map_count = ap_get_map_count(i + 1);
		for (int j = 0; j < map_count; ++j)
		{
			auto level_state = ap_get_level_state(ap_level_index_t{i, j});
			json_get_bool_or(json["episodes"][i][j]["completed"], level_state->completed);
			json_get_bool_or(json["episodes"][i][j]["keys0"], level_state->keys[0]);
			json_get_bool_or(json["episodes"][i][j]["keys1"], level_state->keys[1]);
			json_get_bool_or(json["episodes"][i][j]["keys2"], level_state->keys[2]);
			json_get_bool_or(json["episodes"][i][j]["has_map"], level_state->has_map);
			if (level_state->has_map)
				ap_state.has_computer_area_map = 1;
			json_get_bool_or(json["episodes"][i][j]["unlocked"], level_state->unlocked);
			json_get_bool_or(json["episodes"][i][j]["special"], level_state->special);
			for (int k = 0; k < ap_weapon_count; ++k)
				json_get_bool_or(json["episodes"][i][j]["weapon_owned"][k], level_state->weapon_owned[k]);
			// We don't save checks or check_count -- server state is treated as authoritative
		}
	}
	if (ap_state.vanilla_keys_mode)
		clear_vanilla_key_level_state();

	// Item queue
	for (const auto& item_id_json : json["item_queue"])
	{
		ap_item_queue.push_back(item_id_json.asInt64());
	}

	json_get_int(json["ep"], ap_state.ep);
	printf("  Enabled episodes: ");
	int first = 1;
	for (int i = 0; i < ap_episode_count; ++i)
	{
		json_get_int(json["enabled_episodes"][i], ap_state.episodes[i]);
		if (ap_state.episodes[i])
		{
			if (!first) printf(", ");
			first = 0;
			printf("%i", i + 1);
		}
	}
	printf("\n");
	json_get_int(json["map"], ap_state.map);
	printf("  Episode: %i\n", ap_state.ep);
	printf("  Map: %i\n", ap_state.map);

	for (const auto& prog_json : json["progressive_locations"])
	{
		ap_progressive_locations.insert(prog_json.asInt64());
	}
	for (const auto& useful_json : json["useful_locations"])
	{
		ap_useful_locations.insert(useful_json.asInt64());
	}
	for (const auto& scout_json : json["scouted_locations"])
	{
		ap_scouted_locations.insert(scout_json.asInt64());
	}
	
	json_get_bool_or(json["victory"], ap_state.victory);
	printf("  Victory state: %s\n", ap_state.victory ? "true" : "false");
}


static Json::Value serialize_level(int ep, int map)
{
	auto level_state = ap_get_level_state(ap_level_index_t{ep - 1, map - 1});

	Json::Value json_level;

	json_level["completed"] = level_state->completed;
	json_level["keys0"] = ap_state.vanilla_keys_mode ? 0 : level_state->keys[0];
	json_level["keys1"] = ap_state.vanilla_keys_mode ? 0 : level_state->keys[1];
	json_level["keys2"] = ap_state.vanilla_keys_mode ? 0 : level_state->keys[2];
	json_level["has_map"] = level_state->has_map;
	json_level["unlocked"] = level_state->unlocked;
	json_level["special"] = level_state->special;
	Json::Value json_weapon_owned(Json::arrayValue);
	for (int i = 0; i < ap_weapon_count; ++i)
		json_weapon_owned.append(level_state->weapon_owned[i]);
	json_level["weapon_owned"] = json_weapon_owned;
	// We don't save checks or check_count -- server state is treated as authoritative

	return json_level;
}


void save_state()
{
	if (ap_practice_mode)
		return;

	Json::Value json;

	// Connection info
	if (!ap_practice_mode)
	{
		Json::Value json_launchdata;
		json_launchdata["_last_timestamp"] = (long int)time(NULL);
		json_launchdata["_initial_timestamp"] = initial_timestamp;

		json_launchdata["game"] = ap_settings.game;
		json_launchdata["address"] = ap_settings.ip;
		json_launchdata["slot_name"] = ap_settings.player_name;
		json_launchdata["server_pass"] = ap_settings.passwd;
		if (ap_settings.extra_args)
			json_launchdata["extra_args"] = ap_settings.extra_args;

		Json::Value json_override(Json::objectValue);
		if (ap_settings.override_skill)
			json_override["skill"] = ap_settings.skill + 1; // This matches the -skill parameter.
		if (ap_settings.override_monster_rando)
			json_override["monster_rando"] = ap_settings.monster_rando;
		if (ap_settings.override_item_rando)
			json_override["item_rando"] = ap_settings.item_rando;
		if (ap_settings.override_music_rando)
			json_override["music_rando"] = ap_settings.music_rando;
		if (ap_settings.override_flip_levels)
			json_override["flip_levels"] = ap_settings.flip_levels;
		if (ap_settings.override_reset_level_on_death)
			json_override["reset_level_on_death"] = ap_settings.reset_level_on_death;
		if (ap_settings.force_deathlink_off)
			json_override["no_deathlink"] = 1;
		json_launchdata["overrides"] = json_override;

		json["launcher_data"] = json_launchdata;
	}

	// Player state
	Json::Value json_player;
	json_player["health"] = ap_state.player_state.health;
	json_player["max_health"] = ap_state.player_state.max_health;
	json_player["max_armor"] = ap_state.player_state.max_armor;
	json_player["max_health_bonus_tenths"] = ap_state.max_health_bonus_tenths;
	json_player["max_armor_bonus_tenths"] = ap_state.max_armor_bonus_tenths;
	json_player["enhanced_backpack_count"] = ap_state.enhanced_backpack_count;
	json_player["armor_points"] = ap_state.player_state.armor_points;
	json_player["armor_type"] = ap_state.player_state.armor_type;
	json_player["ready_weapon"] = ap_state.player_state.ready_weapon;
	json_player["kill_count"] = ap_state.player_state.kill_count;
	json_player["item_count"] = ap_state.player_state.item_count;
	json_player["secret_count"] = ap_state.player_state.secret_count;

	Json::Value json_powers(Json::arrayValue);
	for (int i = 0; i < ap_powerup_count; ++i)
		json_powers.append(ap_state.player_state.powers[i]);
	json_player["powers"] = json_powers;

	Json::Value json_weapon_owned(Json::arrayValue);
	for (int i = 0; i < ap_weapon_count; ++i)
		json_weapon_owned.append(ap_state.player_state.weapon_owned[i]);
	json_player["weapon_owned"] = json_weapon_owned;

	Json::Value json_ammo(Json::arrayValue);
	for (int i = 0; i < ap_ammo_count; ++i)
		json_ammo.append(ap_state.player_state.ammo[i]);
	json_player["ammo"] = json_ammo;

	Json::Value json_max_ammo(Json::arrayValue);
	for (int i = 0; i < ap_ammo_count; ++i)
		json_max_ammo.append(ap_state.player_state.max_ammo[i]);
	json_player["max_ammo"] = json_max_ammo;

	Json::Value json_inventory(Json::arrayValue);
	for (int i = 0; i < ap_inventory_count; ++i)
	{
		if (ap_state.player_state.inventory[i].type == 9) // Don't include wings to player inventory, they are per level
			continue;
		Json::Value json_inventory_slot;
		json_inventory_slot["type"] = ap_state.player_state.inventory[i].type;
		json_inventory_slot["count"] = ap_state.player_state.inventory[i].count;
		json_inventory.append(json_inventory_slot);
	}
	json_player["inventory"] = json_inventory;

	json["player"] = json_player;

	// Level states
	Json::Value json_episodes(Json::arrayValue);
	for (int i = 0; i < ap_episode_count; ++i)
	{
		Json::Value json_levels(Json::arrayValue);
		int map_count = ap_get_map_count(i + 1);
		for (int j = 0; j < map_count; ++j)
		{
			json_levels.append(serialize_level(i + 1, j + 1));
		}
		json_episodes.append(json_levels);
	}
	json["episodes"] = json_episodes;

	// Item queue
	Json::Value json_item_queue(Json::arrayValue);
	for (auto item_id : ap_item_queue)
	{
		json_item_queue.append(item_id);
	}
	json["item_queue"] = json_item_queue;

	json["ep"] = ap_state.ep;
	for (int i = 0; i < ap_episode_count; ++i)
		json["enabled_episodes"][i] = ap_state.episodes[i] ? true : false;
	json["map"] = ap_state.map;
	json["has_computer_area_map"] = ap_state.has_computer_area_map;

	// Progression items (So we don't scout everytime we connect)
	for (auto loc_id : ap_progressive_locations)
	{
		json["progressive_locations"].append(loc_id);
	}
	for (auto loc_id : ap_useful_locations)
	{
		json["useful_locations"].append(loc_id);
	}
	for (auto loc_id : ap_scouted_locations)
	{
		json["scouted_locations"].append(loc_id);
	}

	json["victory"] = ap_state.victory;

	json["version"] = APDOOM_VERSION_FULL_TEXT;

	std::ofstream f(ap_save_path / "apstate.json");
	if (!f.is_open())
	{
		printf("Failed to save AP state.\n");
#ifdef _WIN32
		MessageBoxA(nullptr, "Failed to save player state. That's bad.", "Error", MB_OK);
#endif
		return; // Ok that's bad. we won't save player state
	}
	f << json;
}


void f_itemclr()
{
	ap_debug_log_recv("ItemClear: reset received item state");

	// This gets called when (re)connecting to the server.
	// Any items that we need to keep track of, that can be collected multiple times,
	// need to be cleared out here; otherwise, we will double count them on reconnect.
	// Mark sync as authoritative now, before any ItemRecv callbacks arrive. The
	// AP client can clear items before load_state(), and loading saved max stats
	// between clear and replay would apply the same upgrades twice.
	ap_items_synced = true;
	ap_state.player_state.max_health = 200;
	ap_state.player_state.max_armor = 200;
	ap_state.max_health_bonus_tenths = 0;
	ap_state.max_armor_bonus_tenths = 0;
	ap_state.enhanced_backpack_count = 0;
	memset(ap_state.player_state.capacity_upgrades, 0, sizeof(int) * ap_ammo_count);
	recalc_max_ammo();
	for (ap_level_index_t *idx = ap_get_all_levels(); idx->ep != -1; ++idx)
	{
		auto level_state = ap_get_level_state(*idx);
		memset(level_state->weapon_owned, 0, sizeof(int) * ap_weapon_count);
	}
}


static const std::map<int, int> doom_keys_map = {{5, 0}, {40, 0}, {6, 1}, {39, 1}, {13, 2}, {38, 2}};
static const std::map<int, int> doom2_keys_map = {{5, 0}, {40, 0}, {6, 1}, {39, 1}, {13, 2}, {38, 2}};
static const std::map<int, int> heretic_keys_map = {{80, 0}, {73, 1}, {79, 2}};


const std::map<int, int>& get_keys_map()
{
	switch (ap_base_game)
	{
		default: // Indeterminate state? Default to Doom 1
		case ap_game_t::doom: return doom_keys_map;
		case ap_game_t::doom2: return doom2_keys_map;
		case ap_game_t::heretic: return heretic_keys_map;
	}
}


int get_map_doom_type()
{
	switch (ap_base_game)
	{
		default: // Indeterminate state? Default to Doom 1
		case ap_game_t::doom: return 2026;
		case ap_game_t::doom2: return 2026;
		case ap_game_t::heretic: return 35;
	}
}


static const std::map<int, int> doom_weapons_map = {{2001, 2}, {2002, 3}, {2003, 4}, {2004, 5}, {2006, 6}, {2005, 7}};
static const std::map<int, int> doom2_weapons_map = {{2001, 2}, {2002, 3}, {2003, 4}, {2004, 5}, {2006, 6}, {2005, 7}, {82, 8}};
static const std::map<int, int> heretic_weapons_map = {{2005, 7}, {2001, 2}, {53, 3}, {2003, 5}, {2002, 6}, {2004, 4}};


const std::map<int, int>& get_weapons_map()
{
	switch (ap_base_game)
	{
		default: // Indeterminate state? Default to Doom 1
		case ap_game_t::doom: return doom_weapons_map;
		case ap_game_t::doom2: return doom2_weapons_map;
		case ap_game_t::heretic: return heretic_weapons_map;
	}
}


std::string get_exmx_name(const std::string& name)
{
	auto pos = name.find_first_of('(');
	if (pos == std::string::npos) return name;
	return name.substr(pos);
}

static std::string get_display_item_name(const std::string& name)
{
	const std::string progressive_weapon = " - Progressive weapon ";
	auto pos = name.rfind(progressive_weapon);
	if (pos == std::string::npos)
		return name;

	auto step_pos = pos + progressive_weapon.length();
	if (step_pos >= name.length())
		return name;

	for (auto i = step_pos; i < name.length(); ++i)
	{
		if (name[i] < '0' || name[i] > '9')
			return name;
	}

	return name.substr(0, pos) + " - Progressive weapon";
}


static bool is_progressive_weapon_item(const ap_item_t& item)
{
	if (!item.name)
		return false;

	const std::string name = item.name;
	if (ap_state.global_progressive_weapons && name == "Progressive weapon")
		return true;

	if (!ap_state.progressive_weapons)
		return false;

	const std::string progressive_weapon = " - Progressive weapon";
	auto pos = name.rfind(progressive_weapon);
	if (pos == std::string::npos)
		return false;

	auto suffix_pos = pos + progressive_weapon.length();
	if (suffix_pos == name.length())
		return true;

	if (name[suffix_pos] != ' ')
		return false;

	for (auto i = suffix_pos + 1; i < name.length(); ++i)
		if (name[i] < '0' || name[i] > '9')
			return false;

	return suffix_pos + 1 < name.length();
}


static int get_progressive_weapon_doom_type(const int* weapon_owned)
{
	static const int doom_progression[] = {2001, 2002, 2005, 2003, 2004, 2006};
	static const int doom2_progression[] = {2001, 2002, 2005, 82, 2003, 2004, 2006};

	const int* progression = doom_progression;
	int progression_count = sizeof(doom_progression) / sizeof(doom_progression[0]);
	if (ap_base_game == ap_game_t::doom2)
	{
		progression = doom2_progression;
		progression_count = sizeof(doom2_progression) / sizeof(doom2_progression[0]);
	}

	const auto& weapons_map = get_weapons_map();
	for (int i = 0; i < progression_count; ++i)
	{
		auto weapon_it = weapons_map.find(progression[i]);
		if (weapon_it == weapons_map.end())
			continue;
		if (ap_state.remove_chainsaws_from_game && progression[i] == 2005)
			continue;
		if (!weapon_owned || !weapon_owned[weapon_it->second])
			return progression[i];
	}

	return progression[progression_count - 1];
}


static int get_latest_progressive_weapon_doom_type(const int* weapon_owned)
{
	static const int doom_progression[] = {2001, 2002, 2005, 2003, 2004, 2006};
	static const int doom2_progression[] = {2001, 2002, 2005, 82, 2003, 2004, 2006};

	const int* progression = doom_progression;
	int progression_count = sizeof(doom_progression) / sizeof(doom_progression[0]);
	if (ap_base_game == ap_game_t::doom2)
	{
		progression = doom2_progression;
		progression_count = sizeof(doom2_progression) / sizeof(doom2_progression[0]);
	}

	const auto& weapons_map = get_weapons_map();
	for (int i = progression_count - 1; i >= 0; --i)
	{
		auto weapon_it = weapons_map.find(progression[i]);
		if (weapon_it == weapons_map.end())
			continue;
		if (weapon_owned && weapon_owned[weapon_it->second])
			return progression[i];
	}

	return get_progressive_weapon_doom_type(weapon_owned);
}


static bool item_was_stored_as_level_weapon(const ap_item_t& item, int doom_type)
{
	if (!ap_state.level_specific_weapons || item.ep <= 0 || item.map <= 0)
		return false;

	const auto& weapons_map = get_weapons_map();
	auto weapon_it = weapons_map.find(doom_type);
	if (weapon_it == weapons_map.end())
		return false;

	ap_level_state_t* level_state = ap_get_level_state(ap_level_index_t{ item.ep - 1, item.map - 1 });
	return level_state && level_state->weapon_owned[weapon_it->second];
}

static bool enhanced_powerup_items_enabled()
{
	return ap_state.enhanced_ap_powerups || !enhanced_powerup_location_types.empty();
}

static bool is_large_health_upgrade_item(const ap_item_t& item)
{
	return item.name && std::string(item.name) == "Large Health Upgrade";
}

static void add_max_health_tenths(int tenths)
{
	ap_state.max_health_bonus_tenths += tenths;
	ap_state.player_state.max_health = 200 + ap_state.max_health_bonus_tenths / 10;
}

void APDOOM_AddMaxHealth(int amount)
{
	add_max_health_tenths(amount * 10);
}

static void add_max_armor_tenths(int tenths)
{
	ap_state.max_armor_bonus_tenths += tenths;
	ap_state.player_state.max_armor = 200 + ap_state.max_armor_bonus_tenths / 10;
}

static bool apply_enhanced_stat_upgrade(const ap_item_t& item)
{
	if (item.name)
	{
		const std::string name = item.name;
		if (name == "Small Health Upgrade")
		{
			add_max_health_tenths(ap_state.small_health_upgrade);
			return true;
		}
		if (name == "Small Armor Upgrade")
		{
			add_max_armor_tenths(ap_state.small_armor_upgrade);
			return true;
		}
		if (name == "Health Upgrade")
		{
			add_max_health_tenths(ap_state.health_upgrade * 10);
			return true;
		}
		if (name == "Armor Upgrade")
		{
			add_max_armor_tenths(ap_state.armor_upgrade * 10);
			return true;
		}
		if (name == "Big Armor Upgrade")
		{
			add_max_armor_tenths(ap_state.large_armor_upgrade * 10);
			return true;
		}
		if (name == "Large Health Upgrade")
		{
			add_max_health_tenths(ap_state.large_health_upgrade * 10);
			return true;
		}
		if (name == "Mega Life Upgrade")
		{
			add_max_health_tenths(ap_state.mega_upgrade * 10);
			add_max_armor_tenths(ap_state.mega_upgrade * 10);
			return true;
		}
	}

	bool applied = false;
	if (item.doom_type == 2014 && ap_state.enhanced_ap_bonus_items)
	{
		add_max_health_tenths(ap_state.small_health_upgrade);
		applied = true;
	}
	if (item.doom_type == 2015 && ap_state.enhanced_ap_bonus_items)
	{
		add_max_armor_tenths(ap_state.small_armor_upgrade);
		applied = true;
	}

	if (!enhanced_powerup_items_enabled())
		return applied;

	switch (item.doom_type)
	{
	case 2018: // Armor
		add_max_armor_tenths(ap_state.armor_upgrade * 10);
		return true;
	case 2019: // Mega Armor
		add_max_armor_tenths(ap_state.large_armor_upgrade * 10);
		return true;
	case 2022: // Invulnerability
		add_max_health_tenths(ap_state.large_health_upgrade * 10);
		return true;
	case 2013: // Supercharge
		add_max_health_tenths(ap_state.health_upgrade * 10);
		return true;
	case 83: // Megasphere
		add_max_health_tenths(ap_state.mega_upgrade * 10);
		add_max_armor_tenths(ap_state.mega_upgrade * 10);
		return true;
	default:
		break;
	}

	return applied;
}


static void unlock_completed_level_weapons(ap_level_index_t idx)
{
	ap_level_state_t* level_state = ap_get_level_state(idx);
	if (!level_state)
		return;

	if (ap_state.progressive_weapons)
	{
		int progression[8];
		const int progression_count = progressive_weapon_sequence(progression, sizeof(progression) / sizeof(progression[0]));
		const int total = progressive_level_weapon_step_count(idx);
		for (int i = 0; i < total && i < progression_count; ++i)
		{
			const int weapon = progression[i];
			if (weapon >= 0 && weapon < ap_weapon_count)
				level_state->weapon_owned[weapon] = 1;
		}
		return;
	}

	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info)
		return;

	const auto& weapons_map = get_weapons_map();
	for (int i = 0; i < level_info->thing_count; ++i)
	{
		const ap_thing_info_t& thing = level_info->thing_infos[i];
		if (thing.location_id <= 0)
			continue;
		if (suppressed_locations.count(thing.location_id))
			continue;

		auto weapon_it = weapons_map.find(thing.doom_type);
		if (weapon_it == weapons_map.end())
			continue;
		if (ap_state.remove_chainsaws_from_game && weapon_it->second == 7)
			continue;
		if (weapon_it->second >= 0 && weapon_it->second < ap_weapon_count)
			level_state->weapon_owned[weapon_it->second] = 1;
	}
}


static void unlock_completed_level_keys(ap_level_index_t idx)
{
	ap_level_state_t* level_state = ap_get_level_state(idx);
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_state || !level_info)
		return;

	const auto& keys_map = get_keys_map();
	for (int i = 0; i < level_info->thing_count; ++i)
	{
		const ap_thing_info_t& thing = level_info->thing_infos[i];
		if (thing.location_id <= 0)
			continue;
		if (suppressed_locations.count(thing.location_id))
			continue;

		auto key_it = keys_map.find(thing.doom_type);
		if (key_it != keys_map.end())
			level_state->keys[key_it->second] = 1;
	}
}


static void release_remaining_completed_level_checks(ap_level_index_t idx)
{
	bool release_map = ap_state.release_computer_area_map_for_completed_levels != 0;

	if (!release_map)
		return;

	ap_level_state_t* level_state = ap_get_level_state(idx);
	if (level_state)
		level_state->has_map = 1;
	ap_state.has_computer_area_map = 1;
}


// Split from f_itemrecv so that the item queue can call it without side-effects
// This handles everything that requires us be in game, notification icons included
static void process_received_item(int64_t item_id)
{
	ap_item_t item;
	if (!get_ap_item(item_id, item))
		return; // Skip -- This is probably redundant, but whatever

	std::string notif_text;

	// If the item has an associated episode/map, note that
	if (item.ep != -1)
	{
		ap_level_index_t idx = {item.ep - 1, item.map - 1};
		ap_level_info_t* level_info = ap_get_level_info(idx);

		notif_text = get_exmx_name(level_info->name);
	}

	// Give item to in-game player
	int doom_type = item.doom_type;
	if (is_progressive_weapon_item(item) && ap_state.global_progressive_weapons)
	{
		doom_type = get_latest_progressive_weapon_doom_type(ap_state.player_state.weapon_owned);
	}
	else if (is_progressive_weapon_item(item) && item.ep > 0 && item.map > 0)
	{
		ap_level_state_t* level_state = ap_get_level_state(ap_level_index_t{ item.ep - 1, item.map - 1 });
		doom_type = get_latest_progressive_weapon_doom_type(level_state ? level_state->weapon_owned : NULL);
	}

	bool skip_native_enhanced_powerup_effect =
		enhanced_powerup_items_enabled()
		&& (doom_type == 83 || doom_type == 2013 || doom_type == 2018
			|| doom_type == 2019 || is_large_health_upgrade_item(item));

	int given = skip_native_enhanced_powerup_effect
		? true
		: ap_settings.give_item_callback(doom_type, item.ep, item.map);
	if (!given && item_was_stored_as_level_weapon(item, doom_type))
		given = true;

	// Add notification icon
	const char *sprite = ap_get_sprite(doom_type);
	if (sprite)
	{
		ap_notification_icon_t notif;
		snprintf(notif.sprite, 9, "%s", sprite);
		notif.t = 0;
		notif.text[0] = '\0'; // For now
		if (notif_text != "")
		{
			snprintf(notif.text, 40, "%s", notif_text.c_str());
		}
		notif.xf = AP_NOTIF_SIZE / 2 + AP_NOTIF_PADDING;
		notif.yf = -200.0f + AP_NOTIF_SIZE / 2;
		notif.state = AP_NOTIF_STATE_PENDING;
		notif.velx = 0.0f;
		notif.vely = 0.0f;
		notif.x = (int)notif.xf;
		notif.y = (int)notif.yf;
		notif.disabled = !given;
		ap_notification_icons.push_back(notif);
	}
}

void f_itemrecv(int64_t item_id, bool notify_player)
{
	if (!notify_player) // Notify is only false upon receiving a sync packet.
		ap_items_synced = true;

	ap_item_t item;
	if (!get_ap_item(item_id, item))
	{
		if (item_id == 369901 || item_id == 369902 || item_id == 369903
			|| item_id == 369904 || item_id == 369905 || item_id == 369906
			|| item_id == 369907)
		{
			std::ostringstream log;
			log << "ItemRecv unknown upgrade id=" << item_id
				<< " notify=" << (notify_player ? 1 : 0);
			ap_debug_log_recv(log.str());
		}
		return; // Skip
	}

	if (item_id == 369901 || item_id == 369902 || item_id == 369903
		|| item_id == 369904 || item_id == 369905 || item_id == 369906
		|| item_id == 369907)
	{
		std::ostringstream log;
		log << "ItemRecv before id=" << item_id
			<< " doom=" << item.doom_type
			<< " name=" << (item.name ? item.name : "(null)")
			<< " notify=" << (notify_player ? 1 : 0)
			<< " bonus=" << ap_state.enhanced_ap_bonus_items
			<< " powerups=" << ap_state.enhanced_ap_powerups
			<< " max_health=" << ap_state.player_state.max_health
			<< " max_armor=" << ap_state.player_state.max_armor
			<< " health_tenths=" << ap_state.max_health_bonus_tenths
			<< " armor_tenths=" << ap_state.max_armor_bonus_tenths;
		ap_debug_log_recv(log.str());
	}

	ap_level_state_t* level_state = NULL;
	ap_level_index_t item_level_idx = {-1, -1};
	if (item.ep > 0 && item.map > 0)
	{
		item_level_idx = {item.ep - 1, item.map - 1};
		level_state = ap_get_level_state(item_level_idx);
	}

	// Backpack?
	if (item.doom_type == 8)
	{
		if (ap_state.enhanced_ammo
			&& (ap_base_game == ap_game_t::doom || ap_base_game == ap_game_t::doom2))
		{
			++ap_state.enhanced_backpack_count;
		}
		else
		{
			for (int i = 0; i < ap_ammo_count; ++i)
				++ap_state.player_state.capacity_upgrades[i];
			recalc_max_ammo();
		}
	}

	// Single ammo capacity upgrade?
	if (item.doom_type >= 65001 && item.doom_type <= 65006)
	{
		int ammo_num = item.doom_type - 65001;
		if (ammo_num < ap_ammo_count)
			++ap_state.player_state.capacity_upgrades[ammo_num];
		recalc_max_ammo();
	}

	bool applied_stat_upgrade = false;
	if (!notify_player || item.doom_type != 2022 || is_large_health_upgrade_item(item))
		applied_stat_upgrade = apply_enhanced_stat_upgrade(item);

	if (item_id == 369901 || item_id == 369902 || item_id == 369903
		|| item_id == 369904 || item_id == 369905 || item_id == 369906
		|| item_id == 369907)
	{
		std::ostringstream log;
		log << "ItemRecv after id=" << item_id
			<< " applied=" << (applied_stat_upgrade ? 1 : 0)
			<< " max_health=" << ap_state.player_state.max_health
			<< " max_armor=" << ap_state.player_state.max_armor
			<< " health_tenths=" << ap_state.max_health_bonus_tenths
			<< " armor_tenths=" << ap_state.max_armor_bonus_tenths;
		ap_debug_log_recv(log.str());
	}

	if ((!notify_player || applied_stat_upgrade) && ap_settings.refresh_pistol_start_vitals_callback)
		ap_settings.refresh_pistol_start_vitals_callback();

	// Key?
	const auto& keys_map = get_keys_map();
	int doom_type = item.doom_type;
	if (is_progressive_weapon_item(item) && ap_state.global_progressive_weapons)
		doom_type = get_progressive_weapon_doom_type(ap_state.player_state.weapon_owned);
	else if (is_progressive_weapon_item(item) && level_state)
		doom_type = get_progressive_weapon_doom_type(level_state->weapon_owned);

	auto key_it = keys_map.find(doom_type);
	if (level_state && key_it != keys_map.end())
	{
		level_state->keys[key_it->second] = 1;
		if (ap_state.keys_give_map_access)
			level_state->unlocked = 1;
	}

	// Weapon?
	const auto& weapons_map = get_weapons_map();
	auto weapon_it = weapons_map.find(doom_type);
	if (weapon_it != weapons_map.end())
	{
		if ((ap_state.level_specific_weapons || ap_state.progressive_weapons) && level_state)
			level_state->weapon_owned[weapon_it->second] = 1;
		else
			ap_state.player_state.weapon_owned[weapon_it->second] = 1;
	}

	// Map?
	if (level_state && doom_type == get_map_doom_type())
	{
		level_state->has_map = 1;
		ap_state.has_computer_area_map = 1;
		if (!notify_player
			&& ap_is_in_game
			&& item_level_idx.ep == current_level_index.ep
			&& item_level_idx.map == current_level_index.map)
		{
			ap_settings.give_item_callback(doom_type, item.ep, item.map);
		}
	}

	// Level unlock?
	if (level_state && doom_type == -1)
		level_state->unlocked = 1;

	// Level complete?
	if (level_state && doom_type == -2)
	{
		level_state->completed = 1;
		if (notify_player)
			release_remaining_completed_level_checks(item_level_idx);
	}

	// Ignore inventory items, the game will add them up

	if (!notify_player) return;

	if (!ap_is_in_game)
		ap_item_queue.push_back(item_id);
	else
		process_received_item(item_id);
}


bool find_location(int64_t loc_id, int &ep, int &map, int &index)
{
	ep = -1;
	map = -1;
	index = -1;

	const auto& level_table = get_level_info_table();
	for (const auto& episode : level_table)
	{
		for (const auto& level_info : episode)
		{
			for (int i = 0; i < level_info.thing_count; ++i)
			{
				if (level_info.thing_infos[i].location_id == loc_id)
				{
					ep = level_info.game_episode;
					map = level_info.game_map;
					index = level_info.thing_infos[i].index;
					return true;
				}
			}
		}
	}

	const auto& loc_table = get_location_table();
	for (const auto& loc_map_table : loc_table)
	{
		for (const auto& loc_index_table : loc_map_table.second)
		{
			for (const auto& loc_index : loc_index_table.second)
			{
				if (loc_index.second == loc_id)
				{
					int ap_ep = loc_map_table.first - 1;
					int ap_map = loc_index_table.first - 1;
					if (ap_ep < 0
						|| ap_ep >= (int)level_table.size()
						|| ap_map < 0
						|| ap_map >= (int)level_table[ap_ep].size())
						return false;
					ep = level_table[ap_ep][ap_map].game_episode;
					map = level_table[ap_ep][ap_map].game_map;
					index = loc_index.first;
					break;
				}
			}
			if (ep != -1) break;
		}
		if (ep != -1) break;
	}
	return (ep > 0);
}

static bool find_location_table_entry(ap_level_index_t idx, int index, int64_t &id)
{
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info)
		return false;

	if (index >= 0)
	{
		if (index >= level_info->thing_count)
			return false;
		id = level_info->thing_infos[index].location_id;
		return id > 0;
	}

	const auto& loc_table = get_location_table();

	auto it1 = loc_table.find(idx.ep + 1);
	if (it1 == loc_table.end()) return false;

	auto it2 = it1->second.find(idx.map + 1);
	if (it2 == it1->second.end()) return false;

	auto it3 = it2->second.find(index);
	if (it3 == it2->second.end()) return false;

	id = it3->second;
	return true;
}


void f_locrecv(int64_t loc_id)
{
	// Find where this location is
	int ep = -1;
	int map = -1;
	int index = -1;
	if (!find_location(loc_id, ep, map, index))
	{
		printf("APDOOM: In f_locrecv, loc id not found: %i\n", (int)loc_id);
		return; // Loc not found
	}

	ap_level_index_t idx = ap_try_make_level_index(ep, map);
	if (idx.ep < 0)
	{
		printf("APDOOM: In f_locrecv, level not found: ep %i map %i\n", ep, map);
		return;
	}

	// Make sure we didn't already check it
	if (ap_is_location_checked(idx, index)) return;
	if (index < 0) return;

	auto level_state = ap_get_level_state(idx);
	level_state->checks[level_state->check_count++] = index;

	if (level_state->check_count >= level_state->max_check_count)
		printf("APDOOM: In f_locrecv: more checks made than expected. this WILL write out of bounds, please report this\n");
}


void f_locinfo(std::vector<AP_NetworkItem> loc_infos)
{
	for (const auto& loc_info : loc_infos)
	{
		ap_scouted_locations.insert(loc_info.location);
		ap_item_t item;
		if (get_ap_item(loc_info.item, item))
			ap_scouted_location_item_doom_types[loc_info.location] = item.doom_type;
		if (loc_info.flags & AP_ITEM_FLAG_PROGRESSION)
			ap_progressive_locations.insert(loc_info.location);
		if (loc_info.flags & AP_ITEM_FLAG_USEFUL)
			ap_useful_locations.insert(loc_info.location);
	}
}

void f_itemrecv_apcpp(int64_t item_id, int player, bool notify_player)
{
	(void)player;
	f_itemrecv(item_id, notify_player);
}


const char* apdoom_get_save_dir()
{
	static std::string local_save_path = ap_save_path.string();
	return local_save_path.c_str();
}


void apdoom_remove_save_dir(void)
{
	if (!ap_practice_mode || ap_seed_string.substr(0, 8) != "practmp_")
		return; // Don't attempt to remove anything that isn't a practice save
	// We don't support redirecting temp saves, so this shouldn't be able to do anything nasty.
	std::filesystem::remove_all(ap_save_path);
}


void apdoom_check_location(ap_level_index_t idx, int index)
{
	int64_t id = 0;
	if (!find_location_table_entry(idx, index, id))
		return;
	if (suppressed_locations.count(id))
		return;

	if (ap_practice_mode)
	{
		int doom_type;

		f_locrecv(id);

		// Get the item that's supposed to be in that location.
		ap_level_info_t* level_info = ap_get_level_info(idx);
		if (index < 0) // Virtual completion/reward location for level
			doom_type = 99999;
		else
			doom_type = level_info->thing_infos[index].doom_type;

		const auto& item_type_table = get_item_type_table();
		int64_t item_id = APMinePracticeItemId(item_type_table, doom_type, idx.ep + 1, idx.map + 1);

		// Send the item to ourselves as if we were playing.
		if (item_id)
		{
			ap_fake_item_msg(item_id, "Player");
			f_itemrecv(item_id, true);
		}
		return;
	}

	if (index >= 0)
	{
		if (ap_is_location_checked(idx, index))
		{
			printf("APDOOM: Location already checked\n");
		}
		else
		{
			// No operation -- we wait until AP tells us
			// (see f_locrecv())
		}
	}
	AP_SendItem(id);
}


int apdoom_is_location_progression(ap_level_index_t idx, int index)
{
	int64_t id = 0;
	if (!find_location_table_entry(idx, index, id))
		return 0;

	return (int)ap_progressive_locations.count(id);
}

static bool item_type_should_use_progression_icon(int doom_type)
{
	if (doom_type == -1 || doom_type == -2)
		return true;
	if (doom_type == get_map_doom_type())
		return true;
	if (get_keys_map().count(doom_type))
		return true;
	if (get_weapons_map().count(doom_type))
		return true;
	return false;
}

int apdoom_get_location_ap_icon_type(ap_level_index_t idx, int index, int doom_type)
{
	int64_t id = 0;
	if (!find_location_table_entry(idx, index, id))
		return 0;

	if (ap_progressive_locations.count(id))
	{
		auto item_it = ap_scouted_location_item_doom_types.find(id);
		if (item_it != ap_scouted_location_item_doom_types.end()
			&& item_type_should_use_progression_icon(item_it->second))
			return 20001;
	}

	if (ap_useful_locations.count(id) && (doom_type == 8 || doom_type == get_map_doom_type()))
		return 20000;

	return 0;
}

int apdoom_should_show_location_on_automap(ap_level_index_t idx, int index)
{
	int64_t id = 0;
	if (!find_location_table_entry(idx, index, id))
		return 0;
	if (ap_practice_mode || ap_scouted_locations.empty())
		return 1;
	ap_level_state_t* level_state = ap_get_level_state(idx);
	if (level_state && level_state->has_map)
		return 1;

	return (int)(ap_progressive_locations.count(id) || ap_useful_locations.count(id));
}

int apdoom_get_location_item_doom_type(ap_level_index_t idx, int index)
{
	int64_t id = 0;
	if (!find_location_table_entry(idx, index, id))
		return 0;

	auto item_it = ap_scouted_location_item_doom_types.find(id);
	return (item_it != ap_scouted_location_item_doom_types.end()) ? item_it->second : 0;
}

void apdoom_complete_level(ap_level_index_t idx)
{
	//if (ap_state.level_states[ep - 1][map - 1].completed) return; // Already completed
    ap_level_state_t* level_state = ap_get_level_state(idx);
    level_state->completed = 1;
    if (ap_state.release_computer_area_map_for_completed_levels)
    {
        level_state->has_map = 1;
        ap_state.has_computer_area_map = 1;
    }
	apdoom_check_location(idx, -1); // -1 is complete location
	apdoom_check_location(idx, -2); // -2 is extra level reward location
}


// Attempt to make level index; failure is a possibility
ap_level_index_t ap_try_make_level_index(int gameepisode, int gamemap)
{
	// For PWAD support: Level info struct has gameepisode/gamemap, don't make assumptions
	const auto& table = get_level_info_table();
	for (int ep = 0; ep < (int)table.size(); ++ep)
	{
		for (int map = 0; map < (int)table[ep].size(); ++map)
		{
			if (table[ep][map].game_episode == gameepisode && table[ep][map].game_map == gamemap)
				return {ep, map};
		}
	}
	return {-1, -1};
}

// For when failure can't be a possibility due to array access; default to episode 1 map 1
ap_level_index_t ap_make_level_index(int gameepisode, int gamemap)
{
	ap_level_index_t idx = ap_try_make_level_index(gameepisode, gamemap);
	if (idx.ep == -1)
	{
		printf("APDOOM: Episode %d, Map %d isn't in the Archipelago level table!\n", gameepisode, gamemap);
		return {0, 0};
	}
	return idx;
}

int ap_index_to_ep(ap_level_index_t idx)
{
	const auto& table = get_level_info_table();
	return table[idx.ep][idx.map].game_episode;
}


int ap_index_to_map(ap_level_index_t idx)
{
	const auto& table = get_level_info_table();
	return table[idx.ep][idx.map].game_map;
}


void apdoom_check_victory()
{
	if (ap_state.victory)
	{
		// Silently resend victory state, just in case connection got dropped as we actually won
		AP_StoryComplete();
		return;
	}

	int complete_level_count = 0;

	switch (ap_state.goal)
	{
	case 3: // Specific levels
	case 2: // Random levels
		for (int i = 0; i < ap_state.goal_level_count; ++i)
		{
			if (!ap_get_level_state(ap_state.goal_level_list[i])->completed)
				return;
		}
		break;
	case 1: // Some count of levels
		for (int ep = 0; ep < ap_episode_count; ++ep)
		{
			if (!ap_state.episodes[ep]) continue;
		
			const int map_count = ap_get_map_count(ep + 1);
			for (int map = 0; map < map_count; ++map)
			{
				if (ap_get_level_state(ap_level_index_t{ep, map})->completed)
					++complete_level_count;
			}
		}
		if (complete_level_count < ap_state.goal_level_count)
			return;
		break;
	default: // All levels
		for (int ep = 0; ep < ap_episode_count; ++ep)
		{
			if (!ap_state.episodes[ep]) continue;
		
			const int map_count = ap_get_map_count(ep + 1);
			for (int map = 0; map < map_count; ++map)
			{
				if (!ap_get_level_state(ap_level_index_t{ep, map})->completed)
					return;
			}
		}
		break;
	}

	ap_state.victory = 1;

	AP_StoryComplete();
	ap_settings.victory_callback();
}


void apdoom_send_message(const char* msg)
{
	Json::Value say_packet;
	say_packet[0]["cmd"] = "Say";
	say_packet[0]["text"] = msg;
	Json::FastWriter writer;
	APSend(writer.write(say_packet));
}


const ap_notification_icon_t* ap_get_notification_icons(int* count)
{
	*count = (int)ap_notification_icons.size();
	return ap_notification_icons.data();
}


int ap_get_highest_episode()
{
	int highest = 0;
	for (int i = 0; i < ap_episode_count; ++i)
		if (ap_state.episodes[i])
			highest = i;
	return highest;
}


int ap_validate_doom_location(ap_level_index_t idx, int doom_type, int index)
{
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (index >= level_info->thing_count) return 0;
	if (level_info->thing_infos[index].location_id <= 0) return 0;
	if (level_info->thing_infos[index].doom_type != doom_type) return -1;
	if (suppressed_locations.count(level_info->thing_infos[index].location_id)) return 0;
    return 1;
}

int ap_is_vanilla_key_location(ap_level_index_t idx, int index)
{
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info || index >= level_info->thing_count) return 0;
	int64_t loc_id = level_info->thing_infos[index].location_id;
	return loc_id > 0 && vanilla_key_locations.count(loc_id);
}

int ap_is_vanilla_weapon_location(ap_level_index_t idx, int index)
{
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info || index >= level_info->thing_count) return 0;
	int64_t loc_id = level_info->thing_infos[index].location_id;
	return loc_id > 0 && vanilla_weapon_locations.count(loc_id);
}

void ap_set_current_level_index(ap_level_index_t idx)
{
	current_level_index = idx;
}

void ap_mark_vanilla_key_collected(int card)
{
	(void)card;
	// Vanilla-key pickups are intentionally level-local. The live Doom
	// player card state and in-level savegame own them; AP state must not.
}

int apdoom_get_enhanced_bonus_location_type(ap_level_index_t idx, int index)
{
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info || index >= level_info->thing_count) return 0;
	int64_t loc_id = level_info->thing_infos[index].location_id;
	if (loc_id <= 0) return 0;

	const auto it = enhanced_bonus_location_types.find(loc_id);
	if (it != enhanced_bonus_location_types.end())
		return it->second;

	// Compatibility with older slot data where only vanilla bonus locations existed.
	int doom_type = level_info->thing_infos[index].doom_type;
	if (ap_state.enhanced_ap_bonus_items && (doom_type == 2014 || doom_type == 2015))
		return doom_type;
	return 0;
}

int apdoom_get_enhanced_powerup_location_type(ap_level_index_t idx, int index)
{
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info || index >= level_info->thing_count) return 0;
	int64_t loc_id = level_info->thing_infos[index].location_id;
	if (loc_id <= 0) return 0;

	const auto it = enhanced_powerup_location_types.find(loc_id);
	if (it != enhanced_powerup_location_types.end())
		return it->second;

	int doom_type = level_info->thing_infos[index].doom_type;
	if (ap_state.enhanced_ap_powerups
		&& (doom_type == 83 || doom_type == 2013 || doom_type == 2018 || doom_type == 2019
			|| doom_type == 2022 || doom_type == 2023 || doom_type == 2024))
		return doom_type;
	return 0;
}

int apdoom_get_enhanced_ammo_location_type(ap_level_index_t idx, int index)
{
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info || index < 0 || index >= level_info->thing_count) return 0;
	int64_t loc_id = level_info->thing_infos[index].location_id;
	if (loc_id <= 0) return 0;

	const auto it = enhanced_ammo_location_types.find(loc_id);
	return (it != enhanced_ammo_location_types.end()) ? it->second : 0;
}

int apdoom_get_randomized_pickup_location_type(ap_level_index_t idx, int index)
{
	ap_level_info_t* level_info = ap_get_level_info(idx);
	if (!level_info || index >= level_info->thing_count) return 0;
	int64_t loc_id = level_info->thing_infos[index].location_id;
	if (loc_id <= 0) return 0;

	const auto it = randomized_pickup_location_types.find(loc_id);
	return (it != randomized_pickup_location_types.end()) ? it->second : 0;
}


/*
    black: "000000"
    red: "EE0000"
    green: "00FF7F"  # typically a location
    yellow: "FAFAD2"  # typically other slots/players
    blue: "6495ED"  # typically extra info (such as entrance)
    magenta: "EE00EE"  # typically your slot/player
    cyan: "00EEEE"  # typically regular item
    slateblue: "6D8BE8"  # typically useful item
    plum: "AF99EF"  # typically progression item
    salmon: "FA8072"  # typically trap item
    white: "FFFFFF"  # not used, if you want to change the generic text color change color in Label

    (byte *) &cr_none, // 0 (RED)
    (byte *) &cr_dark, // 1 (DARK RED)
    (byte *) &cr_gray, // 2 (WHITE) normal text
    (byte *) &cr_green, // 3 (GREEN) location
    (byte *) &cr_gold, // 4 (YELLOW) player
    (byte *) &cr_red, // 5 (RED, same as cr_none)
    (byte *) &cr_blue, // 6 (BLUE) extra info such as Entrance
    (byte *) &cr_red2blue, // 7 (BLUE) items
    (byte *) &cr_red2green // 8 (DARK EDGE GREEN)
*/
void apdoom_update()
{
	if (ap_initialized)
	{
		if (!ap_cached_messages.empty())
		{
			for (const auto& cached_msg : ap_cached_messages)
				ap_settings.message_callback(cached_msg.c_str());
			ap_cached_messages.clear();
		}
	}

	while (AP_IsMessagePending())
	{
		AP_Message* msg = AP_GetLatestMessage();

		std::string colored_msg;

		switch (msg->type)
		{
			case AP_MessageType::ItemSend:
			{
				AP_ItemSendMessage* o_msg = static_cast<AP_ItemSendMessage*>(msg);
				colored_msg = "~9" + get_display_item_name(o_msg->item) + "~2 was sent to ~4" + o_msg->recvPlayer;
				break;
			}
			case AP_MessageType::ItemRecv:
			{
				AP_ItemRecvMessage* o_msg = static_cast<AP_ItemRecvMessage*>(msg);
				colored_msg = "~2Received ~9" + get_display_item_name(o_msg->item) + "~2 from ~4" + o_msg->sendPlayer;
				break;
			}
			case AP_MessageType::Hint:
			{
				AP_HintMessage* o_msg = static_cast<AP_HintMessage*>(msg);
				colored_msg = "~9" + get_display_item_name(o_msg->item) + "~2 from ~4" + o_msg->sendPlayer + "~2 to ~4" + o_msg->recvPlayer + "~2 at ~3" + o_msg->location + (o_msg->checked ? " (Checked)" : " (Unchecked)");
				break;
			}
			default:
			{
				colored_msg = "~2" + msg->text;
				break;
			}
		}

		printf("APDOOM: %s\n", msg->text.c_str());

		if (ap_initialized)
			ap_settings.message_callback(colored_msg.c_str());
		else
			ap_cached_messages.push_back(colored_msg);

		AP_ClearLatestMessage();
	}

	// Check if we're in game, then dequeue the items
	if (ap_is_in_game)
	{
		while (!ap_item_queue.empty())
		{
			auto item_id = ap_item_queue.front();
			ap_item_queue.erase(ap_item_queue.begin());
			process_received_item(item_id);
		}
	}

	// Update notification icons. Draw code anchors card centers at 172 + y.
	// Keep the first card just above the K/I/S/C HUD block.
	float previous_y = -29.0f;
	for (auto it = ap_notification_icons.begin(); it != ap_notification_icons.end();)
	{
		auto& notification_icon = *it;

		if (notification_icon.state == AP_NOTIF_STATE_PENDING && previous_y > -100.0f)
		{
			notification_icon.state = AP_NOTIF_STATE_DROPPING;
		}
		if (notification_icon.state == AP_NOTIF_STATE_PENDING)
		{
			++it;
			continue;
		}

		if (notification_icon.state == AP_NOTIF_STATE_DROPPING)
		{
			notification_icon.vely += 0.15f + (float)(ap_notification_icons.size() / 4) * 0.25f;
			if (notification_icon.vely > 8.0f) notification_icon.vely = 8.0f;
			notification_icon.yf += notification_icon.vely;
			if (notification_icon.yf >= previous_y - AP_NOTIF_SIZE - AP_NOTIF_PADDING)
			{
				notification_icon.yf = previous_y - AP_NOTIF_SIZE - AP_NOTIF_PADDING;
				notification_icon.vely *= -0.3f / ((float)(ap_notification_icons.size() / 4) * 0.05f + 1.0f);

				notification_icon.t += (int)ap_notification_icons.size() / 4 + 1; // Faster the more we have queued (4 can display on screen)
				if (notification_icon.t > 350 * 3 / 4) // ~7.5sec
				{
					notification_icon.state = AP_NOTIF_STATE_HIDING;
				}
			}
		}

		if (notification_icon.state == AP_NOTIF_STATE_HIDING)
		{
			notification_icon.velx -= 0.14f + (float)(ap_notification_icons.size() / 4) * 0.1f;
			notification_icon.xf += notification_icon.velx;
			if (notification_icon.xf < -AP_NOTIF_SIZE / 2)
			{
				it = ap_notification_icons.erase(it);
				continue;
			}
		}

		notification_icon.x = (int)notification_icon.xf;
		notification_icon.y = (int)notification_icon.yf;
		previous_y = notification_icon.yf;

		++it;
	}

	ap_energylink_pool_update();
}

// Remote data per slot
void ap_remote_set(const char *key, int per_slot, int value)
{
	if (ap_practice_mode)
		return;

	AP_SetServerDataRequest rq;
	if (per_slot)
		rq.key += "<Slot" + std::to_string(AP_GetPlayerID()) + ">";
	rq.key += key;
	rq.operations = { {"replace", &value} };
	rq.default_value = 0;
	rq.type = AP_DataType::Int;
	rq.want_reply = false;

	AP_SetServerData(&rq);
}

static void ap_fake_item_msg(int item_id, const char *sender)
{
	const auto& item_type_table = get_item_type_table();
	auto it = item_type_table.find(item_id);
	if (it == item_type_table.end())
		return;

	std::string msg = std::string("Received ") + it->second.name + " from " + sender;
	printf("APDOOM: %s\n", msg.c_str());

	std::string colored_msg = std::string("~2Received ~9") + it->second.name + "~2 from ~4" + sender;
	ap_settings.message_callback(colored_msg.c_str());
}

// ----------------------------------------------------------------------------
// Consistent randomness based on seed (xorshift64*)
static uint64_t xorshift_base = 0;
static uint64_t xorshift_seed = 1;

static uint64_t hash_seed(const char *str)
{
    uint64_t hash = 5381;
    int c;

    while ((c = *str++))
        hash = ((hash << 5) + hash) + c; /* hash * 33 + c */

    return hash;
}

void ap_srand(int hash)
{
	if (!xorshift_base)
		xorshift_base = hash_seed(ap_seed_string.c_str());

	xorshift_seed = xorshift_base;
	do {
		xorshift_seed += (hash * 19937) + 1;
	} while (!xorshift_seed);
}

unsigned int ap_rand(void)
{
	xorshift_seed ^= xorshift_seed << 17;
	xorshift_seed ^= xorshift_seed >> 31;
	xorshift_seed ^= xorshift_seed << 8;
	return (unsigned int)((xorshift_seed * 1181783497276652981LL) >> 32);
}

// Fisher-Yates shuffle of an array of ints of given length.
void ap_shuffle(int *arr, int len)
{
	int idx;
	for (int i = len - 1; i > 0; --i)
	{
		if ((idx = ap_rand() % (i + 1)) != i)
			std::swap(arr[idx], arr[i]);
	}
}

// ----------------------------------------------------------------------------
// Lump remapping
std::vector<remap_entry_t> *loaded_remap_table = nullptr;

void ap_init_remap(const char *filename)
{
	std::string lower_filename;
	for (size_t i = 0; i < strlen(filename); ++i)
		lower_filename += tolower((const unsigned char)filename[i]);

	loaded_remap_table = nullptr;
	if (lump_remap_list.count(lower_filename))
		loaded_remap_table = &lump_remap_list[lower_filename];
}

int ap_do_remap(char *lump_name)
{
	if (!loaded_remap_table)
		return false;
	for (remap_entry_t &remap : *loaded_remap_table)
	{
		if (remap.rename(lump_name))
			return true;
	}
	return false;
}

// ----------------------------------------------------------------------------
// DeathLink & Obituary tagging system

static std::string deathlink_message; // From the multiworld to us
static std::string deathlink_sent_msg; // From us to the multiworld
static std::set<std::string> upcoming_obit_tags; // List of obit tags and weights for upcoming death

void f_deathlink(std::string source, std::string cause)
{
	if (!cause.empty() && cause.find_first_not_of(' ') != std::string::npos)
		deathlink_message = "~5" + cause;
	else
		deathlink_message = "~5" + source + " killed you.";
	printf("APDOOM: Received death: %s\n", deathlink_message.c_str());
}

void f_deathlink_apcpp()
{
	f_deathlink("A player", "");
}

void APDOOM_ObitTags_Clear(void)
{
	upcoming_obit_tags.clear();
}

void APDOOM_ObitTags_Add(const char *fmt, ...)
{
	static char buf[32];
	va_list args;

	va_start(args, fmt);
#if defined(WIN32) && _MSC_VER < 1400
	_vsnprintf(buf, 31, fmt, args);
#else
	vsnprintf(buf, 31, fmt, args);
#endif
	buf[31] = 0;
	va_end(args);

	upcoming_obit_tags.insert(buf);
}

const char *APDOOM_SendDeath()
{
	if (!ap_settings.always_show_obituaries)
	{
		if (ap_practice_mode)
			return NULL;
	}

	int best_score = 0;
	int cur_score = 0;
	deathlink_sent_msg = "%YOU% died.";

	for (obituary_t& obit : obituary_list)
	{
		if ((cur_score = obit.score(upcoming_obit_tags)) > best_score)
		{
			best_score = cur_score;
			deathlink_sent_msg = obit.get_text();
		}
	}

	// Show debug info if we're showing the default obituary when forced
	if (ap_settings.always_show_obituaries && best_score == 0)
	{
		bool first = true;
		printf("No obituaries matched the following tags: ");
		for (const std::string &tag : upcoming_obit_tags)
		{
			printf("%s%s", (first ? "" : ", "), tag.c_str());
			first = false;
		}
		printf("\n");
	}

	// We've gotten the best obituary, now put the player's name in there
    size_t pname_marker = deathlink_sent_msg.find("%YOU%");
    if (pname_marker != std::string::npos)
        deathlink_sent_msg.replace(pname_marker, 5, ap_settings.player_name);

    if (!ap_practice_mode)
		AP_DeathLinkSend();
	printf("APDOOM: Sent death: %s\n", deathlink_sent_msg.c_str());

	// Colorize before sending C-side.
	deathlink_sent_msg = "~5" + deathlink_sent_msg;
	return deathlink_sent_msg.c_str();
}

void APDOOM_ClearDeath()
{
	AP_DeathLinkClear();
}

const char *APDOOM_ReceiveDeath()
{
	if (AP_DeathLinkPending())
		return deathlink_message.c_str();
	return NULL;
}

// ----------------------------------------------------------------------------
// Save file reading (for launcher)
static std::vector<ap_savesettings_t> savedata_cache;
char memo_buffer[64 + 1];

const ap_savesettings_t *APDOOM_FindSaves(int *save_count)
{
	ap_savesettings_t tmp_savedata;
	bool insert;

	savedata_cache.clear();
	savedata_cache.reserve(16);

	const std::filesystem::path save_dir(std::filesystem::current_path() / "save");
	if (std::filesystem::is_directory(save_dir))
	{
		const ap_worldinfo_t **games_list = ap_list_worlds();
		for (int i = 0; games_list[i]; ++i)
		{
			const std::filesystem::path game_save_dir(save_dir / games_list[i]->shortname);
			if (!std::filesystem::is_directory(game_save_dir))
				continue;

			tmp_savedata.world = games_list[i];
			for (auto const &entry : std::filesystem::directory_iterator(game_save_dir))
			{
				if (!entry.is_directory())
					continue; // Not a directory?

				std::ifstream f(entry.path() / "apstate.json");
				if (!f.is_open())
					continue; // State json is missing or not openable

				insert = false;
				try
				{
					Json::Value json;
					f >> json;

					if (json["launcher_data"].isObject())
					{
						tmp_savedata.victory = json["victory"].asBool();

						json = json["launcher_data"];
						tmp_savedata.practice_mode = false;
						tmp_savedata.last_timestamp = json["_last_timestamp"].asInt64();
						tmp_savedata.initial_timestamp = json["_initial_timestamp"].asInt64();
						snprintf(tmp_savedata.slot_name, 16 + 1, "%s", json["slot_name"].asCString());
						snprintf(tmp_savedata.address, 128 + 1, "%s", json["address"].asCString());
						snprintf(tmp_savedata.password, 128 + 1, "%s", json["server_pass"].asCString());
						snprintf(tmp_savedata.extra_cmdline, 256 + 1, "%s", json.get("extra_args", "").asCString());

						json = json.get("overrides", Json::objectValue);
						tmp_savedata.skill = json.get("skill", -1).asInt();
						tmp_savedata.monster_rando = json.get("monster_rando", -1).asInt();
						tmp_savedata.item_rando = json.get("item_rando", -1).asInt();
						tmp_savedata.music_rando = json.get("music_rando", -1).asInt();
						tmp_savedata.flip_levels = json.get("flip_levels", -1).asInt();
						tmp_savedata.reset_level = json.get("reset_level_on_death", -1).asInt();
						tmp_savedata.no_deathlink = json.get("no_deathlink", -1).asInt();

						insert = true;
					}
				}
				catch (...) {} // Don't include this save game.
				f.close();

				if (!insert)
					continue;

				std::string path_from_cwd = std::filesystem::relative(entry.path()).string();
				snprintf(tmp_savedata.path, 256 + 1, "%s", path_from_cwd.c_str());

				APDOOM_GetSaveMemo(&tmp_savedata);
				if (!memo_buffer[0])
				{
					time_t save_timestamp = (time_t)tmp_savedata.initial_timestamp;
					strftime(memo_buffer, 64 + 1, "%b %d %Y", localtime(&save_timestamp));
				}

				snprintf(tmp_savedata.description, 64 + 1, "%s: %s", memo_buffer, tmp_savedata.slot_name);
				savedata_cache.emplace_back(tmp_savedata);
			}
		}
	}

	std::sort(savedata_cache.begin(), savedata_cache.end(),
		[](const ap_savesettings_t& a, const ap_savesettings_t& b) { return a.initial_timestamp > b.initial_timestamp; });

	*save_count = savedata_cache.size();
	savedata_cache.emplace_back(ap_savesettings_t{}).world = NULL;
	return savedata_cache.data();
}

const char *APDOOM_GetSaveMemo(const ap_savesettings_t *save)
{
	memset(memo_buffer, 0, sizeof(memo_buffer));

	std::ifstream f(std::filesystem::current_path() / save->path / "memo.txt");
	if (f.is_open())
		f.read(memo_buffer, 64);
	for (char *c = memo_buffer; *c; ++c)
		*c = (*c == '\n' ? ' ' : *c);
	return memo_buffer;
}

int APDOOM_SetSaveMemo(const ap_savesettings_t *save, const char *str)
{
	std::ofstream f(std::filesystem::current_path() / save->path / "memo.txt");
	if (!f.is_open())
		return 0;
	f << str;
	return 1;
}

int APDOOM_DeleteSave(const ap_savesettings_t *save)
{
	std::ifstream f(std::filesystem::current_path() / save->path / "apstate.json");
	if (!f.is_open())
		return 0;

	// Make completely sure we're deleting the right save.
	int confirm = 0;
	try
	{
		Json::Value json;
		f >> json;

		if (json["launcher_data"].isObject() &&
			json["launcher_data"]["_initial_timestamp"].asInt64() == save->initial_timestamp)
		{ confirm = 1; }
	}
	catch (...) { confirm = 0; }

	f.close();
	if (confirm)
		std::filesystem::remove_all(std::filesystem::current_path() / save->path);
	return confirm;
}

// ----------------------------------------------------------------------------
// EnergyLink support
static bool energylink_enabled = false;
static bool energylink_pause_takes = false;
static std::string energylink_pool = "";

static int64_t energylink_available = 0; // Total energy in pool.
static int64_t energylink_unsent_adds = 0; // Positive energy adjustment not sent to server yet
static int64_t energylink_unsent_takes = 0; // Negative energy adjustment not sent to server yet
static int64_t energylink_pending_item = 0;
static int64_t energylink_reply_pending_item = 0;

static int energylink_send_period = 8 * 35;

void f_setreply(AP_SetReply setreply)
{
	if (!energylink_enabled || setreply.key != energylink_pool)
		return;
	std::string valuestr = (*(std::string*)setreply.value);

	if (valuestr[0] == '-')
	{
		energylink_available = 0;
	}
	else if (valuestr.find_first_of('e') != std::string::npos)
	{
		// The Json library does an automatic conversion to double, but we don't care;
		// if the value would be high enough to reach that point, we're over our max anyway.
		energylink_available = AP_ENERGYLINK_MAX;
	}
	else try
	{
		energylink_available = (int64_t)std::min(stoull(valuestr), (long long unsigned int)AP_ENERGYLINK_MAX);
	}
	catch (std::out_of_range &)
	{
		energylink_available = AP_ENERGYLINK_MAX;
	}
	catch (...)
	{
		energylink_available = 0;
	}

	//printf("EnergyLink updated, new energy credit total: %d\n", APDOOM_EnergyLink_DisplayEnergy());
	energylink_pause_takes = false;

	// If this is an EnergyLink message for sending an item, then catch that and "give" the item.
	if (energylink_reply_pending_item)
	{
		int64_t item_id = energylink_reply_pending_item;
		energylink_reply_pending_item = 0;

		if (item_id)
		{
			ap_fake_item_msg(item_id, "EnergyLink");
			f_itemrecv(item_id, true);
		}
	}
}

void f_energylink(std::string json_blob)
{
	std::string custom_pool;

	Json::Value json = AP_ReadJson(json_blob);
	if (json.isInt())
	{
		energylink_enabled = (json.asInt() > 0);
	}
	else if (json.isString())
	{
		energylink_enabled = true;
		custom_pool = json.asString();
	}

	if (energylink_enabled)
	{
		energylink_pool = "EnergyLink0";
		if (!custom_pool.empty())
			energylink_pool += "(" + custom_pool + ")";
		AP_RegisterSetReplyCallback(f_setreply);

		// APCpp would try to set the default to {} (an object) if we asked it to. That's *bad*.
		AP_SetNotify(energylink_pool, AP_DataType::Raw);

		// So we need to do it ourself.
		std::string zero = "0";

		AP_SetServerDataRequest rq;
		rq.key = energylink_pool;
		rq.default_value = &zero;
		rq.type = AP_DataType::Raw;
		rq.want_reply = true;
		rq.operations = { {"default", &zero} };
		AP_SetServerData(&rq);

		printf("APDOOM: EnergyLink initialized. Pool: %s\n", energylink_pool.c_str());
	}
}

static void ap_energylink_pool_update(void)
{
	// Only send updates about once every 8 seconds, unless a Take occurs.
	if (!energylink_enabled || --energylink_send_period >= 0)
		return;

	energylink_send_period = 8*35; // Next update in 8 seconds from now
	if (!energylink_unsent_adds && !energylink_unsent_takes)
		return;

	std::string addstr;
	std::string takestr;
	std::string zero = "0";

	AP_SetServerDataRequest rq;
	rq.key = energylink_pool;
	rq.default_value = &zero;
	rq.type = AP_DataType::Raw;
	rq.want_reply = true;

	if (energylink_unsent_adds)
	{
		addstr = std::to_string(energylink_unsent_adds);
		rq.operations.push_back(AP_DataStorageOperation{"add", &addstr});
	}
	if (energylink_unsent_takes)
	{
		takestr = std::to_string(energylink_unsent_takes * -1);
		rq.operations.push_back(AP_DataStorageOperation{"add", &takestr});
	}
	if (energylink_pending_item)
		energylink_reply_pending_item = energylink_pending_item;
	energylink_unsent_adds = energylink_unsent_takes = energylink_pending_item = 0;

	rq.operations.push_back(AP_DataStorageOperation{"max", &zero});
	AP_SetServerData(&rq);
}

int APDOOM_EnergyLink_Enabled(void)
{
	return (int)energylink_enabled;
}

void APDOOM_EnergyLink_GiveEnergy(int64_t energy)
{
	if (energylink_enabled)
		energylink_unsent_adds += energy;
}

int APDOOM_EnergyLink_TakeEnergyForItem(int64_t energy, int item)
{
	if (!energylink_enabled || energylink_pause_takes || energy > energylink_available)
		return 0;

	energylink_unsent_takes += energy;
	energylink_pending_item = item;
	energylink_send_period = 0; // Force an instant update
	energylink_pause_takes = true;
	return 1;
}

int APDOOM_EnergyLink_DisplayEnergy(void)
{
	return (int)(energylink_available / AP_ENERGYLINK_RATIO);
}

const int* APDOOM_EnergyLink_ShopItemList(int* count)
{
	if (count)
		*count = energylink_shop_items.size() - 1;
	return energylink_shop_items.data();
}
