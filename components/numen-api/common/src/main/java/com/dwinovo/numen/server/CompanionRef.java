// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import java.util.UUID;

/** A companion as seen through the actuator: stable id, name, MC owner UUID, and whether it is live in-world. */
public record CompanionRef(UUID id, String name, UUID owner, boolean alive) {}
