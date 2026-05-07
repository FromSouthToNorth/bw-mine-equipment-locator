---
name: bw-token-manager
description: Fetch and cache bw_token and bw_token_bwmes tokens from a local API endpoint. Use when you need to get tokens for a username, cache them locally for 24 hours, or retrieve cached tokens for use in other skills or workflows.
---

# Bw Token Manager

## Overview

This skill provides a simple script to fetch bw_token and bw_token_bwmes tokens from http://192.168.133.110:33382/bwRuleNode/getUserToken, cache them locally in the current workspace (as bw_tokens.json) for 24 hours **per username**, and retrieve them efficiently.

## Quick Start

### Fetch Tokens

To fetch tokens for a username (uses cache if available):

```bash
python3 scripts/bw_token_manager.py <username>
```

### Force Refresh Cache

To ignore cache and fetch fresh tokens:

```bash
python3 scripts/bw_token_manager.py <username> --force-refresh
```

### Text Output

To get tokens in simple text format instead of JSON:

```bash
python3 scripts/bw_token_manager.py <username> --output text
```

## Cache Details

- **Cache File**: `bw_tokens.json` (stored in current working directory/workspace)
- **TTL**: 24 hours (86400 seconds) per username
- **Format**:
  ```json
  {
    "username1": {
      "timestamp": 1234567890,
      "tokens": {
        "bw_token": "...",
        "bw_token_bwmes": "..."
      }
    },
    "username2": {
      "timestamp": 1234567891,
      "tokens": {
        "bw_token": "...",
        "bw_token_bwmes": "..."
      }
    }
  }
  ```

## Using in Other Skills

To use cached tokens in another skill, read the `bw_tokens.json` file from the current workspace, get the entry for the specific username, and validate the timestamp.

## Resources

### scripts/bw_token_manager.py
The main Python script that handles API calls, caching, and output.

