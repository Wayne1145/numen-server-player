// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;
import com.google.gson.JsonParser;
import org.junit.jupiter.api.Test;
import static org.junit.jupiter.api.Assertions.*;
class ActionDistanceAuditTest {
    @Test void boundsIncludeArraysRadiiAndNonfiniteNumbers() {
        var p=SecurityPolicy.defaults();
        assertNull(p.distanceViolation(JsonParser.parseString("{\"x\":64,\"z\":0}"),0,0,0));
        assertNotNull(p.distanceViolation(JsonParser.parseString("{\"blocks\":[{\"x\":65,\"y\":0,\"z\":0}]}"),0,0,0));
        assertNotNull(p.distanceViolation(JsonParser.parseString("{\"radius\":65}"),0,0,0));
        assertNotNull(p.distanceViolation(JsonParser.parseString("{\"radius\":-1}"),0,0,0));
        assertNotNull(p.distanceViolation(JsonParser.parseString("{\"x\":1e999,\"z\":0}"),0,0,0));
    }
}
