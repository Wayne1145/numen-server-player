// SPDX-License-Identifier: LGPL-3.0-only
// Fork-original (minecraft-numen-server-lab, pure server-side control layer).
// New file; links LGPL-3.0 Numen core (Dwinovo). See LICENSE-NOTICE.md.
package com.dwinovo.numen.server;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.Deque;
import java.util.List;

/**
 * Audit sink for every actuator call. Each record is written to the {@code numen-audit} logger as a
 * single structured line AND kept in a bounded in-memory ring buffer so an operator can read the
 * recent tail (e.g. via {@code /numenctl audit}) without scraping the log file.
 *
 * <p>Fields (per S3 §5.2): timestamp, principal, source, companion, tool, task id, lease id,
 * duration, success/failure, error code. No secrets (tokens/keys) are ever recorded.
 */
public final class AuditLog {

    /** One audited call. */
    public record Record(long timestampMs, String principalId, String source, String companionId,
                         String tool, String taskId, String leaseId, long durationMs,
                         boolean success, String code) {

        public String toLine() {
            return "ts=" + timestampMs
                    + " principal=" + nv(principalId)
                    + " src=" + nv(source)
                    + " companion=" + nv(companionId)
                    + " op=" + nv(tool)
                    + " task=" + nv(taskId)
                    + " lease=" + nv(leaseId)
                    + " dur_ms=" + durationMs
                    + " ok=" + success
                    + " code=" + nv(code);
        }

        private static String nv(String s) {
            return s == null || s.isEmpty() ? "-" : s;
        }
    }

    private static final Logger LOG = LoggerFactory.getLogger("numen-audit");
    private static final int RING_MAX = 256;

    private final Deque<Record> ring = new ArrayDeque<>();

    public synchronized void record(Record r) {
        LOG.info(r.toLine());
        ring.addLast(r);
        while (ring.size() > RING_MAX) ring.removeFirst();
    }

    /** The most recent {@code n} audit records (oldest→newest). */
    public synchronized List<Record> tail(int n) {
        List<Record> all = new ArrayList<>(ring);
        int from = Math.max(0, all.size() - n);
        return all.subList(from, all.size());
    }
}
