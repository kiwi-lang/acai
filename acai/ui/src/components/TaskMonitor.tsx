import { useState, useEffect, useRef, useCallback } from 'react';
import { Box, VStack, HStack, Text, Badge, IconButton, Spinner } from '@chakra-ui/react';
import { runTask, type SSEStream } from '../services/api';
import type { Task } from '../services/types';
import Markdown from './Markdown';

/* ─── Icons ─────────────────────────────────────────────────────── */

const PlayIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none">
        <polygon points="5 3 19 12 5 21 5 3" />
    </svg>
);

const RetryIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="23 4 23 10 17 10" />
        <path d="M20.49 15a9 9 0 1 1-2.12-9.36L23 10" />
    </svg>
);

const StopIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="currentColor" stroke="none">
        <rect x="4" y="4" width="16" height="16" rx="2" />
    </svg>
);

const ScrollDownIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="6 9 12 15 18 9" />
    </svg>
);

/* ─── Types ──────────────────────────────────────────────────────── */

interface LogEntry {
    ts: number;
    kind: 'token' | 'reasoning' | 'tool_start' | 'tool_end' | 'error' | 'heartbeat' | 'meta' | 'status' | 'info';
    text: string;
    detail?: string;
}

interface TaskMonitorProps {
    task: Task;
    initialStatus?: string;
    onStatusChange?: (status: string) => void;
    onDone?: () => void;
    autoStart?: boolean;
}

/* ─── Helpers ────────────────────────────────────────────────────── */

const STATUS_COLORS: Record<string, string> = {
    pending: 'gray', curating: 'purple', ready: 'blue',
    in_progress: 'orange', review: 'cyan', completed: 'green', failed: 'red',
};

function elapsed(ms: number): string {
    const s = Math.floor(ms / 1000);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    if (m < 60) return `${m}m ${rem}s`;
    const h = Math.floor(m / 60);
    return `${h}h ${m % 60}m`;
}

function truncateArgs(args: Record<string, unknown>, maxLen = 120): string {
    const raw = JSON.stringify(args);
    if (raw.length <= maxLen) return raw;
    return raw.slice(0, maxLen) + '…';
}

/* ─── Component ──────────────────────────────────────────────────── */

const TaskMonitor = ({ task, initialStatus, onStatusChange, onDone, autoStart = false }: TaskMonitorProps) => {
    const [logs, setLogs] = useState<LogEntry[]>([]);
    const [status, setStatus] = useState(initialStatus || task.status);
    const [running, setRunning] = useState(false);
    const [startedAt, setStartedAt] = useState<number | null>(null);
    const [elapsedStr, setElapsedStr] = useState('');
    const [autoScroll, setAutoScroll] = useState(true);
    const [tokenBuffer, setTokenBuffer] = useState('');
    const [reasoningBuffer, setReasoningBuffer] = useState('');

    const logEndRef = useRef<HTMLDivElement>(null);
    const scrollRef = useRef<HTMLDivElement>(null);
    const streamRef = useRef<SSEStream | null>(null);
    const tokenBufRef = useRef('');
    const reasoningBufRef = useRef('');

    const push = useCallback((entry: LogEntry) => {
        setLogs(prev => {
            const next = [...prev, entry];
            return next.length > 2000 ? next.slice(-1500) : next;
        });
    }, []);

    /* ── elapsed timer ── */
    useEffect(() => {
        if (!startedAt) { setElapsedStr(''); return; }
        const id = setInterval(() => setElapsedStr(elapsed(Date.now() - startedAt)), 1000);
        return () => clearInterval(id);
    }, [startedAt]);

    /* ── auto-scroll ── */
    useEffect(() => {
        if (autoScroll && logEndRef.current) {
            logEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [logs, tokenBuffer, reasoningBuffer, autoScroll]);

    const handleScroll = useCallback(() => {
        const el = scrollRef.current;
        if (!el) return;
        const atBottom = el.scrollHeight - el.scrollTop <= el.clientHeight + 30;
        setAutoScroll(atBottom);
    }, []);

    /* ── start run ── */
    const startRun = useCallback(async () => {
        setLogs([]);
        setRunning(true);
        setStartedAt(Date.now());
        setStatus('in_progress');
        onStatusChange?.('in_progress');
        tokenBufRef.current = '';
        reasoningBufRef.current = '';
        setTokenBuffer('');
        setReasoningBuffer('');

        push({ ts: Date.now(), kind: 'info', text: `Starting task ${task.id.slice(0, 8)}…` });

        try {
            const { stream } = await runTask(task.id);
            streamRef.current = stream;

            let flushTimer = setInterval(() => {
                const t = tokenBufRef.current;
                const r = reasoningBufRef.current;
                if (t) {
                    tokenBufRef.current = '';
                    setTokenBuffer('');
                    push({ ts: Date.now(), kind: 'token', text: t });
                }
                if (r) {
                    reasoningBufRef.current = '';
                    setReasoningBuffer('');
                    push({ ts: Date.now(), kind: 'reasoning', text: r });
                }
            }, 400);

            stream.addEventListener('meta', (e: MessageEvent) => {
                try {
                    const d = JSON.parse(e.data);
                    push({ ts: Date.now(), kind: 'meta', text: `Conversation ${d.conversation || '?'}`, detail: JSON.stringify(d) });
                } catch { /* ignore */ }
            });

            stream.addEventListener('token', (e: MessageEvent) => {
                try {
                    const d = JSON.parse(e.data);
                    tokenBufRef.current += d.token || '';
                    setTokenBuffer(tokenBufRef.current);
                } catch { /* ignore */ }
            });

            stream.addEventListener('reasoning', (e: MessageEvent) => {
                try {
                    const d = JSON.parse(e.data);
                    reasoningBufRef.current += d.token || '';
                    setReasoningBuffer(reasoningBufRef.current);
                } catch { /* ignore */ }
            });

            stream.addEventListener('tool_start', (e: MessageEvent) => {
                try {
                    const t = tokenBufRef.current;
                    const r = reasoningBufRef.current;
                    if (t) { tokenBufRef.current = ''; setTokenBuffer(''); push({ ts: Date.now(), kind: 'token', text: t }); }
                    if (r) { reasoningBufRef.current = ''; setReasoningBuffer(''); push({ ts: Date.now(), kind: 'reasoning', text: r }); }
                    const d = JSON.parse(e.data);
                    push({
                        ts: Date.now(), kind: 'tool_start',
                        text: d.tool_name || d.tool || '?',
                        detail: d.args ? truncateArgs(d.args, 200) : undefined,
                    });
                } catch { /* ignore */ }
            });

            stream.addEventListener('tool_end', (e: MessageEvent) => {
                try {
                    const d = JSON.parse(e.data);
                    const result = d.result || '';
                    const preview = typeof result === 'string'
                        ? (result.length > 200 ? result.slice(0, 200) + '…' : result)
                        : JSON.stringify(result).slice(0, 200);
                    push({ ts: Date.now(), kind: 'tool_end', text: d.tool_name || d.tool || '?', detail: preview });
                } catch { /* ignore */ }
            });

            stream.addEventListener('heartbeat', () => {
                push({ ts: Date.now(), kind: 'heartbeat', text: 'heartbeat' });
            });

            stream.addEventListener('info', (e: MessageEvent) => {
                try {
                    const d = JSON.parse(e.data);
                    push({ ts: Date.now(), kind: 'info', text: d.message || 'Info' });
                } catch { /* ignore */ }
            });

            stream.addEventListener('task_status', (e: MessageEvent) => {
                try {
                    const d = JSON.parse(e.data);
                    if (d.status) {
                        setStatus(d.status);
                        onStatusChange?.(d.status);
                        push({ ts: Date.now(), kind: 'status', text: `Status → ${d.status}` });
                    }
                } catch { /* ignore */ }
            });

            stream.addEventListener('error', (e: MessageEvent) => {
                try {
                    const d = JSON.parse(e.data);
                    push({ ts: Date.now(), kind: 'error', text: d.message || 'Unknown error', detail: d.traceback });
                } catch {
                    push({ ts: Date.now(), kind: 'error', text: 'Stream error' });
                }
                setStatus('failed');
                onStatusChange?.('failed');
                setRunning(false);
                clearInterval(flushTimer);
            });

            stream.addEventListener('done', () => {
                clearInterval(flushTimer);
                const t = tokenBufRef.current;
                const r = reasoningBufRef.current;
                if (t) { tokenBufRef.current = ''; setTokenBuffer(''); push({ ts: Date.now(), kind: 'token', text: t }); }
                if (r) { reasoningBufRef.current = ''; setReasoningBuffer(''); push({ ts: Date.now(), kind: 'reasoning', text: r }); }
                push({ ts: Date.now(), kind: 'info', text: 'Stream ended.' });
                setRunning(false);
                onDone?.();
            });

            stream.onerror = () => {
                clearInterval(flushTimer);
                push({ ts: Date.now(), kind: 'error', text: 'Connection lost' });
                setRunning(false);
            };

        } catch (err) {
            push({ ts: Date.now(), kind: 'error', text: `Failed to start: ${err}` });
            setStatus('failed');
            onStatusChange?.('failed');
            setRunning(false);
        }
    }, [task.id, push, onStatusChange, onDone]);

    const stopRun = useCallback(() => {
        streamRef.current?.close();
        streamRef.current = null;
        setRunning(false);
        push({ ts: Date.now(), kind: 'info', text: 'Stopped by user.' });
    }, [push]);

    /* ── auto-start ── */
    useEffect(() => {
        if (autoStart && !running && logs.length === 0) {
            startRun();
        }
    // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [autoStart]);

    /* ── cleanup ── */
    useEffect(() => {
        return () => { streamRef.current?.close(); };
    }, []);

    /* ── render helpers ── */
    const renderEntry = (entry: LogEntry, i: number) => {
        const timeStr = new Date(entry.ts).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
        switch (entry.kind) {
            case 'token':
                return (
                    <Box key={i} px={3} py={1}>
                        <Box fontSize="sm" color="var(--text-primary)" whiteSpace="pre-wrap" lineHeight="1.6">
                            <Markdown content={entry.text} />
                        </Box>
                    </Box>
                );
            case 'reasoning':
                return (
                    <Box key={i} px={3} py={1} borderLeft="2px solid" borderColor="purple.400" ml={2} opacity={0.8}>
                        <Text fontSize="2xs" color="purple.400" fontWeight="semibold" mb={0.5}>Reasoning</Text>
                        <Text fontSize="xs" color="var(--text-tertiary)" whiteSpace="pre-wrap" fontFamily="mono">
                            {entry.text}
                        </Text>
                    </Box>
                );
            case 'tool_start':
                return (
                    <HStack key={i} px={3} py={1.5} bg="var(--bg-elevated)" borderRadius="md" mx={2} my={1} gap={2} align="flex-start">
                        <Badge colorScheme="blue" fontSize="2xs" flexShrink={0} mt={0.5}>TOOL</Badge>
                        <Box flex={1} minW={0}>
                            <Text fontSize="xs" fontWeight="semibold" color="var(--text-heading)" fontFamily="mono">
                                {entry.text}
                            </Text>
                            {entry.detail && (
                                <Text fontSize="2xs" color="var(--text-muted)" fontFamily="mono" whiteSpace="pre-wrap" lineClamp={3}>
                                    {entry.detail}
                                </Text>
                            )}
                        </Box>
                        <Text fontSize="2xs" color="var(--text-muted)" flexShrink={0}>{timeStr}</Text>
                    </HStack>
                );
            case 'tool_end':
                return (
                    <HStack key={i} px={3} py={1} mx={2} gap={2} align="flex-start" opacity={0.7}>
                        <Badge colorScheme="green" fontSize="2xs" flexShrink={0} mt={0.5}>DONE</Badge>
                        <Box flex={1} minW={0}>
                            <Text fontSize="2xs" color="var(--text-secondary)" fontFamily="mono">
                                {entry.text}
                            </Text>
                            {entry.detail && (
                                <Text fontSize="2xs" color="var(--text-muted)" fontFamily="mono" whiteSpace="pre-wrap" lineClamp={2}>
                                    {entry.detail}
                                </Text>
                            )}
                        </Box>
                        <Text fontSize="2xs" color="var(--text-muted)" flexShrink={0}>{timeStr}</Text>
                    </HStack>
                );
            case 'error':
                return (
                    <Box key={i} px={3} py={2} bg="var(--bg-error)" borderRadius="md" mx={2} my={1}>
                        <HStack gap={2} mb={entry.detail ? 1 : 0}>
                            <Badge colorScheme="red" fontSize="2xs">ERROR</Badge>
                            <Text fontSize="xs" color="var(--text-error)" fontWeight="medium" flex={1}>{entry.text}</Text>
                            <Text fontSize="2xs" color="var(--text-muted)">{timeStr}</Text>
                        </HStack>
                        {entry.detail && (
                            <Text fontSize="2xs" color="var(--text-error)" fontFamily="mono" whiteSpace="pre-wrap" maxH="200px" overflowY="auto" opacity={0.8}>
                                {entry.detail}
                            </Text>
                        )}
                    </Box>
                );
            case 'heartbeat':
                return (
                    <HStack key={i} px={3} py={0.5} gap={2} opacity={0.4}>
                        <Text fontSize="2xs" color="var(--text-muted)">♡ heartbeat</Text>
                        <Text fontSize="2xs" color="var(--text-muted)">{timeStr}</Text>
                    </HStack>
                );
            case 'meta':
            case 'status':
            case 'info':
                return (
                    <HStack key={i} px={3} py={1} gap={2}>
                        <Badge colorScheme={entry.kind === 'status' ? 'teal' : 'gray'} fontSize="2xs" variant="subtle">
                            {entry.kind.toUpperCase()}
                        </Badge>
                        <Text fontSize="xs" color="var(--text-secondary)">{entry.text}</Text>
                        <Text fontSize="2xs" color="var(--text-muted)" ml="auto">{timeStr}</Text>
                    </HStack>
                );
            default:
                return null;
        }
    };

    const canStart = !running && status !== 'in_progress';

    return (
        <Box display="flex" flexDirection="column" h="100%">
            {/* Header bar */}
            <HStack
                px={4} py={3}
                borderBottom="1px solid" borderColor="var(--border-primary)"
                bg="var(--bg-elevated)" flexShrink={0}
                justify="space-between"
            >
                <HStack gap={3} flex={1} minW={0}>
                    <Text fontSize="sm" fontWeight="semibold" color="var(--text-heading)" lineClamp={1}>
                        {task.title}
                    </Text>
                    <Badge colorScheme={STATUS_COLORS[status] || 'gray'} fontSize="2xs">{status}</Badge>
                    {elapsedStr && (
                        <Text fontSize="2xs" color="var(--text-muted)" fontFamily="mono" flexShrink={0}>{elapsedStr}</Text>
                    )}
                    {running && <Spinner size="xs" color="var(--accent)" />}
                </HStack>
                <HStack gap={1}>
                    {running ? (
                        <IconButton
                            aria-label="Stop"
                            variant="ghost" size="sm"
                            color="var(--text-error, #e53e3e)"
                            _hover={{ bg: 'var(--bg-input)' }}
                            onClick={stopRun}
                            title="Stop monitoring (does not cancel the task)"
                        >
                            <StopIcon />
                        </IconButton>
                    ) : canStart && (
                        <IconButton
                            aria-label={status === 'failed' ? 'Retry' : 'Run'}
                            variant="ghost" size="sm"
                            color={status === 'failed' ? 'var(--text-error, #e53e3e)' : 'var(--accent)'}
                            _hover={{ bg: 'var(--bg-input)' }}
                            onClick={startRun}
                            title={status === 'failed' ? 'Retry this task' : 'Run this task'}
                        >
                            {status === 'failed' ? <RetryIcon /> : <PlayIcon />}
                        </IconButton>
                    )}
                </HStack>
            </HStack>

            {/* Log body */}
            <Box
                ref={scrollRef}
                flex={1}
                overflowY="auto"
                py={2}
                onScroll={handleScroll}
                bg="var(--bg-page)"
            >
                {logs.length === 0 && !running && !tokenBuffer && !reasoningBuffer ? (
                    <VStack py={12} gap={2}>
                        <Text fontSize="sm" color="var(--text-muted)">No logs yet.</Text>
                        {canStart && (
                            <Text fontSize="xs" color="var(--text-muted)">
                                Click {status === 'failed' ? 'retry' : 'play'} to start the task.
                            </Text>
                        )}
                    </VStack>
                ) : (
                    <VStack gap={0} align="stretch">
                        {logs.map((entry, i) => renderEntry(entry, i))}
                        {/* live token buffer */}
                        {tokenBuffer && (
                            <Box px={3} py={1}>
                                <Box fontSize="sm" color="var(--text-primary)" whiteSpace="pre-wrap" lineHeight="1.6" opacity={0.7}>
                                    <Markdown content={tokenBuffer} />
                                </Box>
                            </Box>
                        )}
                        {reasoningBuffer && (
                            <Box px={3} py={1} borderLeft="2px solid" borderColor="purple.400" ml={2} opacity={0.5}>
                                <Text fontSize="xs" color="var(--text-tertiary)" whiteSpace="pre-wrap" fontFamily="mono">
                                    {reasoningBuffer}
                                </Text>
                            </Box>
                        )}
                        <div ref={logEndRef} />
                    </VStack>
                )}
            </Box>

            {/* Scroll-to-bottom fab */}
            {!autoScroll && (
                <IconButton
                    aria-label="Scroll to bottom"
                    position="absolute" bottom={4} right={4}
                    size="sm" borderRadius="full"
                    bg="var(--bg-card)" color="var(--text-secondary)"
                    boxShadow="md"
                    border="1px solid" borderColor="var(--border-primary)"
                    _hover={{ bg: 'var(--bg-card-hover)' }}
                    onClick={() => {
                        setAutoScroll(true);
                        logEndRef.current?.scrollIntoView({ behavior: 'smooth' });
                    }}
                >
                    <ScrollDownIcon />
                </IconButton>
            )}
        </Box>
    );
};

export default TaskMonitor;
