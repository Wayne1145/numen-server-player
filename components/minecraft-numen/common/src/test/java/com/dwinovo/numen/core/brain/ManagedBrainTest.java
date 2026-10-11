package com.dwinovo.numen.core.brain;

import org.junit.jupiter.api.Test;
import java.nio.file.*;
import java.io.*;
import java.util.zip.*;
import static org.junit.jupiter.api.Assertions.*;

class ManagedBrainTest {
    byte[] zip(String path) throws Exception {
        var b = new ByteArrayOutputStream();
        try (var z = new ZipOutputStream(b)) {
            z.putNextEntry(new ZipEntry(path)); z.write("test".getBytes()); z.closeEntry();
        }
        return b.toByteArray();
    }
    @Test void refusesZipTraversal() throws Exception {
        var root = Files.createTempDirectory("brain-test");
        assertThrows(IOException.class, () -> ManagedBrain.extract(new ByteArrayInputStream(zip("../escape")), root));
    }
    @Test void missingModelConfigDoesNotStartAChild() throws Exception {
        var root = Files.createTempDirectory("brain-no-model");
        ManagedBrain.prepare(root, new ByteArrayInputStream(zip("managed_entry.py")));
        try (var brain = new ManagedBrain(root)) {
            assertTrue(brain.start().startsWith("setup required"));
        }
    }
    @Test void repeatedPreparationKeepsTokenAndMode() throws Exception {
        var root = Files.createTempDirectory("brain-repeat");
        ManagedBrain.prepare(root, new ByteArrayInputStream(zip("managed_entry.py")));
        String policy = Files.readString(root.resolve("server_control.json"));
        String mode = Files.readString(root.resolve("managed_brain.json"));
        ManagedBrain.prepare(root, new ByteArrayInputStream(zip("managed_entry.py")));
        assertEquals(policy, Files.readString(root.resolve("server_control.json")));
        assertEquals(mode, Files.readString(root.resolve("managed_brain.json")));
    }
    @Test void preservesExistingOperatorConfiguration() throws Exception {
        var root = Files.createTempDirectory("brain-config");
        var c = root.resolve("server_control.json");
        Files.writeString(c, "operator-owned");
        ManagedBrain.prepare(root, new ByteArrayInputStream(zip("managed_entry.py")));
        assertEquals("operator-owned", Files.readString(c));
        assertTrue(Files.exists(root.resolve("brain/managed_entry.py")));
        assertTrue(Files.readString(root.resolve("managed_brain.json")).contains("managed"));
    }
}
