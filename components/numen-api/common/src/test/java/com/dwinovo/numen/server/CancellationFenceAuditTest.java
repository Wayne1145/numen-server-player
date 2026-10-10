// SPDX-License-Identifier: LGPL-3.0-only
package com.dwinovo.numen.server;
import org.junit.jupiter.api.Test;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;
class CancellationFenceAuditTest {
    @Test void cancellationFenceSurvivesRegistryRestart() throws Exception {
        var root=java.nio.file.Files.createTempDirectory("numen-generation");
        var id=UUID.randomUUID();
        ActionGeneration.configure(root);
        long before=ActionGeneration.current(id);
        ActionGeneration.cancel(id);
        ActionGeneration.configure(root);
        assertEquals(before+1,ActionGeneration.current(id));
    }
    @Test void staleGenerationIsRejectedAfterCancel() {
        UUID id=UUID.randomUUID();
        long old=ActionGeneration.current(id);
        ActionGeneration.cancel(id);
        assertFalse(ActionGeneration.matches(id,old));
        assertTrue(ActionGeneration.matches(id,ActionGeneration.current(id)));
    }
}
