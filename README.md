# Sandro's Add-ons Repository

This repository contains stable Home Assistant Add-ons for advanced version control, history management, AI agent access, and GitHub syncing.

## Add-ons included

- **[Home Assistant Editor](https://github.com/saihgupr/HomeAssistantEditor)**: A sleek, three-column visual workspace for taming massive automations and scripts that are impossible to read in the standard UI.
- **[Home Assistant Version Control](https://github.com/saihgupr/HomeAssistantVersionControl)**: Automatic backup, history tracking, and instant restore for your Home Assistant configuration.
- **[Home Assistant Time Machine](https://github.com/saihgupr/HomeAssistantTimeMachine)**: A web-based tool that acts as a "Time Machine" for your Home Assistant configuration.
- **[Home Assistant Agent Gateway](https://github.com/sandro-defender/ha-mcp)** (`ha-mcp`): A policy-controlled gateway that lets AI coding agents (MCP/REST) operate Home Assistant, Proxmox VE, and restricted SSH targets without ever receiving your credentials.
- **[GitHub Sync](https://github.com/sandro-defender/Github-Sync)**: Sync any Home Assistant folder with a GitHub repository. Each folder can target a different repo, with a sidebar UI, file browser, and smart gitignore editor.

## Installation

[![Open your Home Assistant instance and show the add-on store via My Home Assistant.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Fsandro-defender%2Fha-addons)

Or manually:

1. Navigate to **Settings** > **Add-ons** > **Add-on Store** in Home Assistant.
2. Click the three dots (menu) in the top right corner and select **Repositories**.
3. Add the URL of this repository: `https://github.com/sandro-defender/ha-addons`.
4. Click **Add**.
5. Find the add-ons in the store and install them!

## Updates

Each add-on is included as a git submodule pinned to an upstream commit, and Home Assistant only ever sees the pinned version — so the pins are kept current automatically: [`.github/workflows/sync-addons.yml`](.github/workflows/sync-addons.yml) runs hourly and fast-forwards any add-on whose upstream `version:` changed. New releases then appear in Home Assistant within a few hours (or immediately via **Add-on Store → ⋮ → Check for updates**). See [`docs/UPDATES.md`](docs/UPDATES.md) for how this works, the alternatives, and troubleshooting.

## Looking for beta versions?

Check out the [beta add-ons repository](https://github.com/saihgupr/ha-addons-beta) for experimental features.
