import { useState, useEffect, useRef, useCallback, KeyboardEvent, useLayoutEffect, type ReactNode } from 'react';
import { Box, VStack, HStack, Text, Textarea, IconButton, Spinner, NativeSelect } from '@chakra-ui/react';
import { converse, uberConverse, thinkConverse, getHistory, listProviders, listAgents, listGraphs, checkInflight, getContextStats, type SSEStream, type GraphDef } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentDef, AgentMessage, Provider } from '../services/types';
import Markdown from './Markdown';

/* ─── Reasoning display ──────────────────────────────────────────── */

const ReasoningBlock = ({ content }: { content: string }) => {
    const [expanded, setExpanded] = useState(false);
    if (!content) return null;
    return (
        <Box
            borderRadius="md"
            border="1px solid" borderColor="var(--border-secondary)"
            overflow="hidden" fontSize="xs" w="100%" mb={2}
        >
            <HStack px={3} py={1.5} gap={2} cursor="pointer"
                onClick={() => setExpanded(!expanded)}
                _hover={{ bg: 'var(--bg-hover)' }}>
                <Text fontSize="xs" color="var(--text-muted)">
                    {expanded ? '▼' : '▶'}
                </Text>
                <Text color="var(--text-secondary)" fontSize="xs" fontStyle="italic">
                    Reasoning
                </Text>
                <Text color="var(--text-muted)" fontSize="2xs" ml="auto">
                    {content.length > 1000
                        ? `${(content.length / 1000).toFixed(1)}k chars`
                        : `${content.length} chars`}
                </Text>
            </HStack>
            {expanded && (
                <Box px={3} py={2} borderTop="1px solid" borderColor="var(--border-primary)"
                    fontSize="xs" color="var(--text-tertiary)"
                    maxH="300px" overflowY="auto"
                    bg="var(--bg-page)" opacity={0.85}>
                    <Markdown content={content} fontSize="xs" />
                </Box>
            )}
        </Box>
    );
};

/* ─── Icons ──────────────────────────────────────────────────────── */

const SendIcon = ({ size = 20 }: { size?: number }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
);

const ResendIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="1 4 1 10 7 10" />
        <path d="M3.51 15a9 9 0 1 0 2.13-9.36L1 10" />
    </svg>
);

const ToolIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M14.7 6.3a1 1 0 000 1.4l1.6 1.6a1 1 0 001.4 0l3.77-3.77a6 6 0 01-7.94 7.94l-6.91 6.91a2.12 2.12 0 01-3-3l6.91-6.91a6 6 0 017.94-7.94l-3.76 3.76z" />
    </svg>
);

type ThinkingMode = 'off' | 'native' | 'emulated';

export const ContextRing = ({ tokens, maxTokens }: { tokens: number; maxTokens: number }) => {
    const ratio = Math.min(tokens / maxTokens, 1);
    const r = 9;
    const circ = 2 * Math.PI * r;
    const offset = circ * (1 - ratio);
    const color = ratio > 0.8 ? 'var(--text-error, #e53e3e)' : ratio > 0.5 ? '#dd6b20' : 'var(--accent, #38a169)';
    const fmt = (n: number) => n >= 1000 ? `${(n / 1000).toFixed(1)}k` : String(n);

    return (
        <Box title={`~${fmt(tokens)} / ${fmt(maxTokens)} tokens (${(ratio * 100).toFixed(0)}%)`} cursor="default" flexShrink={0}>
            <svg width="22" height="22" viewBox="0 0 22 22">
                <circle cx="11" cy="11" r={r} fill="none" stroke="var(--border-primary)" strokeWidth="2.5" />
                <circle cx="11" cy="11" r={r} fill="none"
                    stroke={color} strokeWidth="2.5"
                    strokeDasharray={circ} strokeDashoffset={offset}
                    strokeLinecap="round"
                    transform="rotate(-90 11 11)"
                    style={{ transition: 'stroke-dashoffset 0.3s ease, stroke 0.3s ease' }}
                />
            </svg>
        </Box>
    );
};

const UserIcon = () => (
    <Box w="28px" h="28px" bg="purple.500" borderRadius="sm"
        display="flex" alignItems="center" justifyContent="center"
        color="var(--text-inverse)" fontWeight="bold" fontSize="xs" flexShrink={0}>
        U
    </Box>
);

const AssistantIcon = () => (
    <Box w="28px" h="28px" bg="var(--bg-brand-icon)" borderRadius="sm"
        display="flex" alignItems="center" justifyContent="center"
        color="var(--text-inverse)" fontWeight="bold" fontSize="xs" flexShrink={0}>
        AI
    </Box>
);

/* ─── ToolCallCard ───────────────────────────────────────────────── */

const ToolCallCard = ({ callMsg, resultMsg }: { callMsg: AgentMessage; resultMsg?: AgentMessage }) => {
    const [expanded, setExpanded] = useState(false);
    const pending = !resultMsg;
    let parsed: { tool?: string; args?: Record<string, unknown> } = {};
    try { parsed = JSON.parse(callMsg.content); } catch { /* use raw */ }
    const toolName = callMsg.name || parsed.tool || 'tool';
    const argsText = parsed.args ? JSON.stringify(parsed.args, null, 2) : callMsg.content;
    const resultText = resultMsg?.content || '';

    return (
        <Box
            borderRadius="md"
            border="1px solid" borderColor="var(--border-secondary)"
            overflow="hidden" fontSize="xs" w="100%"
        >
            <HStack px={3} py={1.5} gap={2} cursor="pointer"
                onClick={() => setExpanded(!expanded)}
                _hover={{ bg: 'var(--bg-hover)' }}>
                {pending ? (
                    <Spinner size="xs" color="var(--accent)" />
                ) : (
                    <Box color="var(--accent)"><ToolIcon /></Box>
                )}
                <Text color="var(--text-secondary)" flex={1} fontFamily="mono" fontSize="xs">
                    {toolName}
                </Text>
                {pending && (
                    <Text color="var(--text-muted)" fontSize="2xs" fontStyle="italic">running</Text>
                )}
                <Text color="var(--text-muted)" fontSize="2xs">{expanded ? '▼' : '▶'}</Text>
            </HStack>
            {expanded && (
                <Box px={3} py={2} borderTop="1px solid" borderColor="var(--border-primary)"
                    fontFamily="mono" fontSize="xs" color="var(--text-secondary)"
                    whiteSpace="pre-wrap" maxH="240px" overflowY="auto">
                    <Text fontSize="2xs" fontWeight="bold" color="var(--text-muted)" mb={1}>IN</Text>
                    <Box mb={2}>{argsText || '(none)'}</Box>
                    {resultMsg && (
                        <>
                            <Text fontSize="2xs" fontWeight="bold" color="var(--text-muted)" mb={1}>OUT</Text>
                            <Box>{resultText || '(empty)'}</Box>
                        </>
                    )}
                </Box>
            )}
        </Box>
    );
};

/* ─── ChatPanel ──────────────────────────────────────────────────── */

export type ChatMode = 'converse' | 'uber';

export interface ChatPanelProps {
    conversationId: string | null;
    onConversationCreated?: (id: string) => void;
    project?: string;
    /** When `project` is set, default chat agent comes from project definition (`refiner`); this overrides the fallback slug. */
    refinerAgent?: string;
    compact?: boolean;
    initialProvider?: string;
    initialAgent?: string;
    onProviderChange?: (v: string) => void;
    onAgentChange?: (v: string) => void;
    /** Chat mode: 'converse' (default) sends to /converse; 'uber' sends to /uber/converse which routes then converses. */
    mode?: ChatMode;
    /** Called when an uber `route` event arrives with the routed conversation. */
    onRoute?: (data: { conversation: string; is_new: boolean; title: string }) => void;
    /** Rendered between the messages area and the input area. */
    statusBar?: ReactNode;
    /** Externally disable the input (e.g. during routing). */
    disabled?: boolean;
    /** Called when a streaming response completes. */
    onResponseComplete?: () => void;
    /** Custom placeholder for the text input. */
    placeholder?: string;
    /** Initial thinking mode (from conversation metadata). */
    initialThinking?: boolean;
    /** Initial thinking mode string — takes precedence over initialThinking boolean. */
    initialThinkingMode?: ThinkingMode;
    /** Auto-send this message on mount (used for pending messages from navigation). */
    autoSendMessage?: string;
    /** Force a specific graph selection (e.g. "workflow:my-id"). Hides the graph dropdown when set. */
    initialGraph?: string;
    /** Ephemeral mode — don't create or persist conversations (for test/preview chat). */
    ephemeral?: boolean;
    /** Task id — conversations will be scoped under workspace/projects/<project>/<taskId>/. */
    taskId?: string;
}

const ChatPanel = ({
    conversationId,
    onConversationCreated,
    project,
    refinerAgent,
    compact = false,
    initialProvider = 'auto',
    initialAgent,
    onProviderChange,
    onAgentChange,
    mode = 'converse',
    onRoute,
    statusBar,
    disabled: externalDisabled,
    onResponseComplete,
    placeholder:     customPlaceholder,
    initialThinking,
    initialThinkingMode,
    autoSendMessage,
    initialGraph,
    ephemeral = false,
    taskId,
}: ChatPanelProps) => {
    const fallbackAgent = project ? (refinerAgent ?? 'refiner') : 'default';
    const resolvedInitialAgent = initialAgent ?? fallbackAgent;

    const [messages, setMessages] = useState<AgentMessage[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [selectedProvider, setSelectedProvider] = useState(
        () => initialProvider || localStorage.getItem('acai.provider') || 'auto',
    );
    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [selectedAgent, setSelectedAgent] = useState(
        () => initialAgent ?? localStorage.getItem('acai.agent') ?? fallbackAgent,
    );
    const [graphs, setGraphs] = useState<GraphDef[]>([]);
    const [selectedGraph, setSelectedGraph] = useState(
        () => initialGraph || localStorage.getItem('acai.graph') || 'converse',
    );
    const [contextStats, setContextStats] = useState<{ estimated_tokens: number; max_context: number } | null>(null);
    const [thinkingMode, setThinkingMode] = useState<ThinkingMode>(
        () => initialThinkingMode
            ?? (initialThinking === false ? 'off' : undefined)
            ?? (localStorage.getItem('acai.thinking') as ThinkingMode | null)
            ?? 'native',
    );

    interface RoutePending {
        conversation: string;
        is_new: boolean;
        title: string;
        message: string;
        countdown: number;
    }
    const [routePending, setRoutePending] = useState<RoutePending | null>(null);
    const routeTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const pendingMessageRef = useRef<string>('');

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const shouldRestoreFocusRef = useRef(false);
    const activeTaskRef = useRef<string | null>(null);
    const eventSourceRef = useRef<EventSource | SSEStream | null>(null);
    const convIdRef = useRef<string | null>(conversationId);
    const justCreatedRef = useRef(false);

    const initialProviderRef = useRef(initialProvider);
    const initialAgentRef = useRef(resolvedInitialAgent);
    initialProviderRef.current = initialProvider;
    initialAgentRef.current = resolvedInitialAgent;

    useEffect(() => {
        if (initialGraph) setSelectedGraph(initialGraph);
    }, [initialGraph]);

    useEffect(() => {
        if (conversationId) return;
        const fb = project ? (refinerAgent ?? 'refiner') : 'default';
        const next = initialAgent ?? fb;
        initialAgentRef.current = next;
        setSelectedAgent(next);
    }, [conversationId, project, refinerAgent, initialAgent]);

    const onProviderChangeRef = useRef(onProviderChange);
    const onAgentChangeRef = useRef(onAgentChange);
    const onResponseCompleteRef = useRef(onResponseComplete);
    const onRouteRef = useRef(onRoute);
    const onConversationCreatedRef = useRef(onConversationCreated);
    const handleSendRef = useRef<(text?: string) => void>(undefined);
    onProviderChangeRef.current = onProviderChange;
    onAgentChangeRef.current = onAgentChange;
    onResponseCompleteRef.current = onResponseComplete;
    onRouteRef.current = onRoute;
    onConversationCreatedRef.current = onConversationCreated;
    const ephemeralRef = useRef(ephemeral);
    ephemeralRef.current = ephemeral;

    const { joinConversation, leaveConversation } = useAgentSocket();

    useEffect(() => {
        listProviders().then(setProviders).catch(() => {});
        listAgents().then(setAgents).catch(() => {});
        listGraphs().then(setGraphs).catch(() => {});
    }, []);

    const closeEventSource = useCallback(() => {
        const es = eventSourceRef.current;
        if (es) {
            eventSourceRef.current = null;
            es.onerror = null;
            es.close();
        }
    }, []);

    const clearRouteTimer = useCallback(() => {
        if (routeTimerRef.current) {
            clearInterval(routeTimerRef.current);
            routeTimerRef.current = null;
        }
    }, []);

    const attachListeners = useCallback((es: EventSource | SSEStream) => {
        closeEventSource();
        eventSourceRef.current = es;

        const _handleConvSwitch = (newConvId: string) => {
            if (newConvId && newConvId !== convIdRef.current) {
                const prevId = convIdRef.current;
                convIdRef.current = newConvId;
                if (!ephemeralRef.current) {
                    joinConversation(newConvId);
                    if (prevId) leaveConversation(prevId);
                    justCreatedRef.current = true;
                    onConversationCreatedRef.current?.(newConvId);
                }
            }
        };

        es.addEventListener('meta', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            _handleConvSwitch(data.conversation);
        });

        es.addEventListener('route', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            onRouteRef.current?.(data);
            setRoutePending({
                conversation: data.conversation,
                is_new: data.is_new,
                title: data.title || '',
                message: pendingMessageRef.current,
                countdown: 5,
            });
        });

        es.addEventListener('reasoning', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = {
                        ...last,
                        reasoning: (last.reasoning || '') + (data.token || ''),
                    };
                } else {
                    copy.push({ role: 'assistant', content: '', reasoning: data.token || '', isStreaming: true });
                }
                return copy;
            });
        });

        es.addEventListener('token', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, content: last.content + (data.token || '') };
                } else {
                    copy.push({ role: 'assistant', content: data.token || '', isStreaming: true });
                }
                return copy;
            });
        });

        es.addEventListener('tool_start', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, isStreaming: false };
                }
                copy.push({
                    role: 'tool_call' as const,
                    content: JSON.stringify({ tool: data.tool_name, args: data.args }, null, 2),
                    name: data.tool_name,
                });
                return copy;
            });
        });

        es.addEventListener('tool_end', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            setMessages(prev => [...prev, {
                role: 'tool_result' as const,
                content: data.result_preview || '(done)',
                name: data.tool_name,
            }]);
        });

        for (const [evt, phase] of [['curator_token', 'curator'], ['scribe_token', 'scribe']] as const) {
            es.addEventListener(evt, (e: MessageEvent) => {
                const data = JSON.parse(e.data);
                const token = data.token || '';
                setMessages(prev => {
                    const copy = [...prev];
                    const last = copy[copy.length - 1];
                    if (last && last.role === 'phase' && last.phase === phase && last.phaseStatus === 'streaming') {
                        copy[copy.length - 1] = { ...last, content: last.content + token };
                    } else {
                        if (last && last.isStreaming) {
                            copy[copy.length - 1] = { ...last, isStreaming: false };
                        }
                        copy.push({ role: 'phase' as const, content: token, phase, phaseStatus: 'streaming' });
                    }
                    return copy;
                });
            });
        }

        const phaseEvents = [
            'curator_start', 'curator_end',
            'curator_tool_start', 'curator_tool_end',
            'scribe_start', 'scribe_end',
            'scribe_tool_start', 'scribe_tool_end',
        ];
        for (const evt of phaseEvents) {
            es.addEventListener(evt, (e: MessageEvent) => {
                const data = JSON.parse(e.data);
                const [phase, ...rest] = evt.split('_');
                const status = rest.join('_');
                const content = status === 'tool_start'
                    ? `${data.tool_name}(${Object.keys(data.args || {}).join(', ')})`
                    : status === 'tool_end'
                        ? data.result_preview || '(done)'
                        : status === 'start'
                            ? `${data.agent || phase} starting...`
                            : data.document_count !== undefined
                                ? `Done — ${data.document_count} document${data.document_count !== 1 ? 's' : ''} selected`
                                : data.status || 'done';
                setMessages(prev => [...prev, {
                    role: 'phase' as const,
                    content,
                    phase,
                    phaseStatus: status,
                    name: data.tool_name,
                }]);
            });
        }

        es.addEventListener('print', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last?.isStreaming) copy[copy.length - 1] = { ...last, isStreaming: false };
                copy.push({
                    role: 'print' as const,
                    content: data.text || '',
                    nodeLabel: data.label || 'Print',
                });
                return copy;
            });
        });

        es.addEventListener('done', () => {
            setMessages(prev =>
                prev.map(m => m.isStreaming ? { ...m, isStreaming: false } : m),
            );
            activeTaskRef.current = null;
            setIsLoading(false);
            closeEventSource();
            onResponseCompleteRef.current?.();
        });

        es.addEventListener('error', (e: MessageEvent) => {
            let errorMsg = 'Stream error';
            let tracebackStr = '';
            try {
                const data = JSON.parse(e.data);
                errorMsg = data.message || data.error || errorMsg;
                tracebackStr = data.traceback || '';
            } catch { /* raw error event from EventSource */ }
            const detail = tracebackStr
                ? `${errorMsg}\n\n\`\`\`\n${tracebackStr}\`\`\``
                : errorMsg;
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, isStreaming: false, error: detail };
                } else {
                    copy.push({ role: 'assistant', content: '', error: detail });
                }
                return copy;
            });
            activeTaskRef.current = null;
            setIsLoading(false);
            closeEventSource();
        });

        es.onerror = (evt?: Event | string) => {
            const reason = typeof evt === 'string' ? evt : 'Connection lost';
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, content: last.content || '', isStreaming: false, error: reason };
                } else {
                    copy.push({ role: 'assistant', content: '', error: reason });
                }
                return copy;
            });
            activeTaskRef.current = null;
            setIsLoading(false);
            closeEventSource();
        };
    }, [closeEventSource]);

    const openEventSource = useCallback((convId: string) => {
        attachListeners(new EventSource(`/api/agent/stream/${convId}`));
    }, [attachListeners]);

    useEffect(() => {
        return () => closeEventSource();
    }, [closeEventSource]);

    const autoSendRef = useRef(autoSendMessage);

    const loadConversation = useCallback(async (convId: string, pending?: string, signal?: { cancelled: boolean }) => {
        let history: AgentMessage[] = [];
        try {
            const resp = await getHistory(convId);
            if (signal?.cancelled) return;
            if (pending) autoSendRef.current = undefined;
            history = resp.messages;

            if (resp.streaming) {
                const tid = resp.streaming.task_id;
                activeTaskRef.current = tid;
                setIsLoading(true);
                setMessages([
                    ...history,
                    { role: 'assistant', content: resp.streaming!.partial, isStreaming: true, taskId: tid },
                ]);
                openEventSource(convId);
                return;
            }
        } catch (err) {
            if (signal?.cancelled) return;
            const reason = err instanceof Error ? err.message : 'Failed to load conversation';
            setMessages([{ role: 'assistant', content: '', error: `Could not load history: ${reason}` }]);
            setIsLoading(false);
            return;
        }

        if (!pending) {
            setMessages(history);
            return;
        }

        setMessages([...history, { role: 'user', content: pending }]);
        setIsLoading(true);

        try {
            const think = initialThinkingMode ?? (initialThinking === false ? 'off' : 'native');
            if (think === 'emulated') {
                const r = await thinkConverse(pending, convId, project || '', '',
                    initialProviderRef.current, initialAgentRef.current, taskId);
                if (signal?.cancelled) { r.stream.close(); return; }
                setMessages(prev => [...prev, { role: 'assistant', content: '', isStreaming: true }]);
                attachListeners(r.stream);
            } else {
                const r = await converse(pending, convId, project || '', '',
                    initialProviderRef.current, initialAgentRef.current,
                    think === 'native' ? true : undefined, selectedGraph,
                    ephemeral || undefined, taskId);
                if (signal?.cancelled) { r.stream.close(); return; }
                setMessages(prev => [...prev, { role: 'assistant', content: '', isStreaming: true }]);
                attachListeners(r.stream);
            }
        } catch (err) {
            if (signal?.cancelled) return;
            const detail = err instanceof Error ? err.message : String(err);
            setMessages(prev => [...prev, { role: 'assistant', content: '', error: detail }]);
            setIsLoading(false);
        }
    }, [openEventSource, attachListeners, initialThinkingMode, initialThinking, project, taskId]);

    const loadConversationRef = useRef(loadConversation);
    loadConversationRef.current = loadConversation;

    useEffect(() => {
        convIdRef.current = conversationId;

        if (justCreatedRef.current) {
            justCreatedRef.current = false;
            return;
        }

        const signal = { cancelled: false };

        setSelectedProvider(initialProviderRef.current);
        setSelectedAgent(initialAgentRef.current);
        setThinkingMode(initialThinkingMode ?? (initialThinking === false ? 'off' : 'native'));
        setMessages([]);
        setIsLoading(false);
        setRoutePending(null);
        clearRouteTimer();
        activeTaskRef.current = null;
        closeEventSource();

        if (!conversationId) {
            if (autoSendRef.current) {
                const pending = autoSendRef.current;
                autoSendRef.current = undefined;
                handleSendRef.current?.(pending);
            }
            return () => { signal.cancelled = true; };
        }

        loadConversationRef.current(conversationId, autoSendRef.current, signal);

        return () => { signal.cancelled = true; };
    }, [conversationId, closeEventSource, clearRouteTimer]);

    useEffect(() => {
        if (conversationId) joinConversation(conversationId);
        return () => {
            if (conversationId) leaveConversation(conversationId);
        };
    }, [conversationId, joinConversation, leaveConversation]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, [messages]);

    useEffect(() => {
        const chars = messages.reduce((sum, m) => sum + (m.content?.length || 0), 0);
        setContextStats(prev => ({
            estimated_tokens: Math.round(chars / 4),
            max_context: prev?.max_context || 128000,
        }));
    }, [messages]);

    useEffect(() => {
        if (conversationId) {
            getContextStats(conversationId).then(setContextStats).catch(() => {});
        }
    }, [conversationId]);

    useLayoutEffect(() => {
        if (shouldRestoreFocusRef.current && textareaRef.current && !isLoading && !externalDisabled) {
            const id = setTimeout(() => {
                textareaRef.current?.focus();
                shouldRestoreFocusRef.current = false;
            }, 50);
            return () => clearTimeout(id);
        }
    }, [input, isLoading, externalDisabled]);

    /* ── Actions ── */

    const handleProviderChangeInternal = useCallback((value: string) => {
        setSelectedProvider(value);
        localStorage.setItem('acai.provider', value);
        onProviderChangeRef.current?.(value);
    }, []);

    const handleAgentChangeInternal = useCallback((value: string) => {
        setSelectedAgent(value);
        localStorage.setItem('acai.agent', value);
        onAgentChangeRef.current?.(value);
    }, []);

    const startConverse = useCallback(async (text: string, targetConvId: string) => {
        setRoutePending(null);
        clearRouteTimer();

        const prevId = convIdRef.current;
        convIdRef.current = targetConvId;
        joinConversation(targetConvId);
        if (prevId && prevId !== targetConvId) leaveConversation(prevId);
        justCreatedRef.current = true;
        onConversationCreatedRef.current?.(targetConvId);

        setIsLoading(true);
        try {
            const resp = await converse(text, targetConvId, project || '', '', selectedProvider, selectedAgent,
                thinkingMode === 'native' ? true : undefined, selectedGraph,
                ephemeral || undefined, taskId);
            setMessages(prev => [
                ...prev,
                { role: 'assistant', content: '', isStreaming: true },
            ]);
            attachListeners(resp.stream);
        } catch (err) {
            const detail = err instanceof Error ? err.message : String(err);
            setMessages(prev => [...prev, { role: 'assistant', content: '', error: detail }]);
            setIsLoading(false);
        }
    }, [project, selectedProvider, selectedAgent, thinkingMode, joinConversation, leaveConversation, attachListeners, clearRouteTimer, ephemeral, taskId]);

    const acceptRoute = useCallback(() => {
        if (!routePending) return;
        startConverse(routePending.message, routePending.conversation);
    }, [routePending, startConverse]);

    const rejectRoute = useCallback(async () => {
        if (!routePending) return;
        const text = routePending.message;
        setRoutePending(null);
        clearRouteTimer();

        setIsLoading(true);
        try {
            const resp = await converse(text, '', project || '', '', selectedProvider, selectedAgent,
                thinkingMode === 'native' ? true : undefined, selectedGraph,
                ephemeral || undefined, taskId);
            setMessages(prev => [
                ...prev,
                { role: 'assistant', content: '', isStreaming: true },
            ]);
            attachListeners(resp.stream);
        } catch (err) {
            const detail = err instanceof Error ? err.message : String(err);
            setMessages(prev => [...prev, { role: 'assistant', content: '', error: detail }]);
            setIsLoading(false);
        }
    }, [routePending, project, selectedProvider, selectedAgent, thinkingMode, attachListeners, clearRouteTimer, taskId]);

    useEffect(() => {
        if (!routePending) return;
        clearRouteTimer();
        routeTimerRef.current = setInterval(() => {
            setRoutePending(prev => {
                if (!prev) return null;
                if (prev.countdown <= 1) return { ...prev, countdown: 0 };
                return { ...prev, countdown: prev.countdown - 1 };
            });
        }, 1000);
        return clearRouteTimer;
    }, [routePending?.conversation, clearRouteTimer]);

    useEffect(() => {
        if (routePending && routePending.countdown <= 0) {
            acceptRoute();
        }
    }, [routePending?.countdown]);

    const handleResend = useCallback(async () => {
        const cid = convIdRef.current;
        if (!cid || isLoading) return;
        const lastUserMsg = [...messages].reverse().find(m => m.role === 'user');
        if (!lastUserMsg) return;

        const inflightResp = await checkInflight(cid).catch(() => ({ inflight: false }));
        if (inflightResp.inflight) return;

        setIsLoading(true);
        try {
            if (thinkingMode === 'emulated') {
                const resp = await thinkConverse(lastUserMsg.content, cid, project || '', '', selectedProvider, selectedAgent, taskId);
                setMessages(prev => [
                    ...prev,
                    { role: 'assistant', content: '', isStreaming: true },
                ]);
                attachListeners(resp.stream);
            } else {
                const resp = await converse(lastUserMsg.content, cid, project || '', '', selectedProvider, selectedAgent,
                    thinkingMode === 'native' ? true : undefined, selectedGraph,
                    ephemeral || undefined, taskId);
                setMessages(prev => [
                    ...prev,
                    { role: 'assistant', content: '', isStreaming: true },
                ]);
                attachListeners(resp.stream);
            }
        } catch (err) {
            const detail = err instanceof Error ? err.message : String(err);
            setMessages(prev => [...prev, { role: 'assistant', content: '', error: detail }]);
            setIsLoading(false);
        }
    }, [isLoading, messages, project, selectedProvider, selectedAgent, thinkingMode, attachListeners, taskId]);

    const handleSend = async (overrideText?: string) => {
        const text = (overrideText ?? input).trim();
        if (!text || isLoading || externalDisabled) return;

        if (!overrideText) {
            if (document.activeElement === textareaRef.current) {
                shouldRestoreFocusRef.current = true;
            }
            setInput('');
            if (textareaRef.current) textareaRef.current.style.height = 'auto';
        }

        setMessages(prev => [...prev, { role: 'user', content: text }]);
        setIsLoading(true);

        const prevConvId = convIdRef.current;

        try {
            if (mode === 'uber') {
                pendingMessageRef.current = text;
                const resp = await uberConverse(text, prevConvId || '', selectedAgent);
                attachListeners(resp.stream);
            } else if (thinkingMode === 'emulated') {
                const resp = await thinkConverse(text, prevConvId || '', project || '', '', selectedProvider, selectedAgent, taskId);
                convIdRef.current = resp.conversation;
                joinConversation(resp.conversation);
                const convChanged = resp.conversation !== (prevConvId || '');
                if (convChanged) {
                    if (prevConvId) leaveConversation(prevConvId);
                    justCreatedRef.current = true;
                    onConversationCreated?.(resp.conversation);
                }
                setMessages(prev => [
                    ...prev,
                    { role: 'assistant', content: '', isStreaming: true },
                ]);
                attachListeners(resp.stream);
            } else {
                const resp = await converse(text, prevConvId || '', project || '', '', selectedProvider, selectedAgent,
                    thinkingMode === 'native' ? true : undefined, selectedGraph,
                    ephemeral || undefined, taskId);
                convIdRef.current = resp.conversation;
                if (!ephemeral) {
                    joinConversation(resp.conversation);
                    const convChanged = resp.conversation !== (prevConvId || '');
                    if (convChanged) {
                        if (prevConvId) leaveConversation(prevConvId);
                        justCreatedRef.current = true;
                        onConversationCreated?.(resp.conversation);
                    }
                }
                setMessages(prev => [
                    ...prev,
                    { role: 'assistant', content: '', isStreaming: true },
                ]);
                attachListeners(resp.stream);
            }
        } catch (err) {
            const detail = err instanceof Error ? err.message : String(err);
            setMessages(prev => [...prev, { role: 'assistant', content: '', error: detail }]);
            setIsLoading(false);
        }
    };

    handleSendRef.current = handleSend;

    const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        setInput(e.target.value);
        e.target.style.height = 'auto';
        e.target.style.height = Math.min(e.target.scrollHeight, compact ? 160 : 200) + 'px';
    };

    /* ── Render ── */

    const maxW = compact ? undefined : '48rem';
    const mx = compact ? undefined : 'auto';

    return (
        <Box flex={1} display="flex" flexDirection="column" overflow="hidden" h="100%">
            {/* Messages */}
            <Box flex={1} overflowY="auto" w="100%" minH={0}>
                {messages.length === 0 ? (
                    <VStack flex={1} justify="center" align="center"
                        p={compact ? 4 : 8} gap={compact ? 4 : 6}
                        minH={compact ? '200px' : '60vh'}>
                        {!compact && (
                            <Box w="64px" h="64px" bg="var(--bg-brand-icon)" borderRadius="xl"
                                display="flex" alignItems="center" justifyContent="center"
                                fontSize="2xl" color="var(--text-inverse)" fontWeight="bold">
                                AI
                            </Box>
                        )}
                        <Text fontSize={compact ? 'xs' : 'md'} color="var(--text-tertiary)" textAlign="center" maxW="md">
                            {compact
                                ? 'Chat with the agent about this project.'
                                : conversationId
                                    ? 'This conversation is empty. Send a message to start.'
                                    : 'Start a new conversation or pick one from the sidebar.'}
                        </Text>
                    </VStack>
                ) : (
                    <VStack gap={0} w="100%">
                        {messages.map((msg, i) => {
                            const isLastMsg = i === messages.length - 1;
                            const showResend = isLastMsg && msg.role === 'user' && !isLoading;

                            if (msg.role === 'tool_result') {
                                const prev = messages.slice(0, i).reverse()
                                    .find(m => m.role === 'tool_call' && m.name === msg.name);
                                if (prev) return null;
                            }

                            const isSameTurnAsAssistant = (idx: number) => {
                                for (let j = idx - 1; j >= 0; j--) {
                                    const r = messages[j].role;
                                    if (r === 'tool_call' || r === 'tool_result') continue;
                                    return r === 'assistant';
                                }
                                return false;
                            };

                            const isToolContinuation = msg.role === 'assistant' && i > 0 &&
                                ['tool_call', 'tool_result'].includes(messages[i - 1]?.role);

                            if (msg.role === 'phase') {
                                const isStart = msg.phaseStatus === 'start';
                                const isEnd = msg.phaseStatus === 'end';
                                const isTool = msg.phaseStatus?.startsWith('tool');
                                const phaseName = msg.phase === 'curator' ? 'Curator' : msg.phase === 'scribe' ? 'Scribe' : msg.phase;
                                const color = msg.phase === 'curator' ? '#667eea' : '#e6a817';
                                const phaseEnded = messages.slice(i + 1).some(
                                    m => m.role === 'phase' && m.phase === msg.phase && m.phaseStatus === 'end',
                                );

                                if (isStart) {
                                    return (
                                        <Box key={i} w="100%" py={1.5} px={compact ? 3 : 4}>
                                            <HStack maxW={maxW} mx={mx} gap={2}>
                                                <Box w="6px" h="6px" borderRadius="full" bg={color} flexShrink={0} />
                                                {!phaseEnded && <Spinner size="xs" color={color} />}
                                                <Text fontSize="xs" fontWeight="semibold" color={color}>
                                                    {phaseName}
                                                </Text>
                                                <Text fontSize="xs" color="var(--text-muted)">{msg.content}</Text>
                                            </HStack>
                                        </Box>
                                    );
                                }

                                if (isTool) {
                                    const isToolEnd = msg.phaseStatus === 'tool_end';
                                    return (
                                        <Box key={i} w="100%" py={0.5} px={compact ? 3 : 4}>
                                            <HStack maxW={maxW} mx={mx} gap={2} pl="14px">
                                                <Box color={color}><ToolIcon /></Box>
                                                <Text fontSize="xs" color="var(--text-muted)" fontFamily="mono" flex={1} truncate>
                                                    {msg.content}
                                                </Text>
                                                {!isToolEnd && !phaseEnded && <Spinner size="xs" color={color} />}
                                            </HStack>
                                        </Box>
                                    );
                                }

                                if (isEnd) {
                                    return (
                                        <Box key={i} w="100%" py={1.5} px={compact ? 3 : 4}>
                                            <HStack maxW={maxW} mx={mx} gap={2}>
                                                <Box w="6px" h="6px" borderRadius="full" bg={color} flexShrink={0} />
                                                <Text fontSize="xs" fontWeight="semibold" color={color}>
                                                    {phaseName}
                                                </Text>
                                                <Text fontSize="xs" color="var(--text-muted)">{msg.content}</Text>
                                            </HStack>
                                        </Box>
                                    );
                                }

                                if (msg.phaseStatus === 'streaming') {
                                    return (
                                        <Box key={i} w="100%" py={1.5} px={compact ? 3 : 4}>
                                            <Box maxW={maxW} mx={mx}>
                                                <HStack gap={2} mb={1}>
                                                    <Box w="6px" h="6px" borderRadius="full" bg={color} flexShrink={0} />
                                                    {!phaseEnded && <Spinner size="xs" color={color} />}
                                                    <Text fontSize="xs" fontWeight="semibold" color={color}>
                                                        {phaseName}
                                                    </Text>
                                                </HStack>
                                                <Box pl="14px" fontSize="xs" color={color + 'cc'} lineHeight="1.5"
                                                     whiteSpace="pre-wrap" fontFamily="mono" maxH="120px" overflowY="auto">
                                                    {msg.content}
                                                </Box>
                                            </Box>
                                        </Box>
                                    );
                                }

                                return null;
                            }

                            if (msg.role === 'print') {
                                const isLong = msg.content.length > 300;
                                return (
                                    <Box key={i} w="100%" py={1.5} px={compact ? 3 : 4}>
                                        <Box maxW={maxW} mx={mx}
                                            borderRadius="md" overflow="hidden"
                                            border="1px solid" borderColor="var(--accent)"
                                            bg="color-mix(in srgb, var(--accent) 8%, transparent)">
                                            <HStack px={3} py={1.5} gap={2}
                                                borderBottom="1px solid" borderColor="color-mix(in srgb, var(--accent) 20%, transparent)">
                                                <Box w="6px" h="6px" borderRadius="sm" bg="var(--accent)" flexShrink={0} />
                                                <Text fontSize="xs" fontWeight="semibold" color="var(--accent)">
                                                    {msg.nodeLabel || 'Print'}
                                                </Text>
                                                <Text fontSize="xs" color="var(--text-muted)" ml="auto">
                                                    {msg.content.length > 1000
                                                        ? `${(msg.content.length / 1000).toFixed(1)}k chars`
                                                        : `${msg.content.length} chars`}
                                                </Text>
                                            </HStack>
                                            <Box px={3} py={2} fontSize="xs" lineHeight="1.5"
                                                whiteSpace="pre-wrap" wordBreak="break-word"
                                                fontFamily="mono" color="var(--text-secondary)"
                                                maxH={isLong ? '200px' : undefined} overflowY={isLong ? 'auto' : undefined}>
                                                {msg.content || '(empty)'}
                                            </Box>
                                        </Box>
                                    </Box>
                                );
                            }

                            if (msg.role === 'tool_call') {
                                const result = messages.slice(i + 1)
                                    .find(m => m.role === 'tool_result' && m.name === msg.name);
                                const showBadge = !isSameTurnAsAssistant(i);
                                return (
                                    <Box key={i} w="100%" bg="var(--bg-card)" py={compact ? 2 : 3} px={compact ? 3 : 4}>
                                        <HStack maxW={maxW} mx={mx} align="flex-start" gap={compact ? 2 : 4}>
                                            {showBadge ? <AssistantIcon /> : <Box w="28px" flexShrink={0} />}
                                            <VStack align="flex-start" flex={1} gap={1}>
                                                {showBadge && (
                                                    <Text fontWeight="semibold" fontSize={compact ? 'xs' : 'sm'} color="var(--text-assistant-label)">
                                                        Agent
                                                    </Text>
                                                )}
                                                <ToolCallCard callMsg={msg} resultMsg={result} />
                                            </VStack>
                                        </HStack>
                                    </Box>
                                );
                            }

                            if (isToolContinuation) {
                                return (
                                    <Box key={i} w="100%" bg="var(--bg-card)"
                                        py={compact ? 3 : 6} px={compact ? 3 : 4}>
                                        <HStack maxW={maxW} mx={mx} align="flex-start" gap={compact ? 2 : 4}>
                                            <Box w="28px" flexShrink={0} />
                                            <VStack align="flex-start" flex={1} gap={1}>
                                                <Markdown content={msg.content} fontSize={compact ? 'sm' : 'md'} />
                                                {msg.isStreaming && (
                                                    <Box as="span" display="inline-block" w="2px" h="1em"
                                                        bg="var(--cursor-blink)" ml={0.5}
                                                        animation="blink 1s step-start infinite" />
                                                )}
                                            </VStack>
                                        </HStack>
                                    </Box>
                                );
                            }

                            return (
                                <Box key={i} w="100%"
                                    bg={msg.role === 'user' ? 'transparent' : 'var(--bg-card)'}
                                    py={compact ? 3 : 6} px={compact ? 3 : 4}>
                                    <HStack maxW={maxW} mx={mx} align="flex-start" gap={compact ? 2 : 4}>
                                        {msg.role === 'user' ? <UserIcon /> : <AssistantIcon />}
                                        <VStack align="flex-start" flex={1} gap={1}>
                                            <Text fontWeight="semibold" fontSize={compact ? 'xs' : 'sm'}
                                                color={msg.role === 'user' ? 'var(--text-user-label)' : 'var(--text-assistant-label)'}>
                                                {msg.role === 'user' ? 'You' : 'Agent'}
                                            </Text>
                                            {msg.role === 'assistant' && msg.reasoning && (
                                                <ReasoningBlock content={msg.reasoning} />
                                            )}
                                            <Markdown content={msg.content} fontSize={compact ? 'sm' : 'md'} />
                                            {msg.isStreaming && !msg.content && msg.reasoning && (
                                                <Text fontSize="xs" color="var(--text-muted)" fontStyle="italic">
                                                    Thinking...
                                                </Text>
                                            )}
                                            {msg.isStreaming && (
                                                <Box as="span" display="inline-block" w="2px" h="1em"
                                                    bg="var(--cursor-blink)" ml={0.5}
                                                    animation="blink 1s step-start infinite" />
                                            )}
                                            {msg.error && (
                                                <Box mt={1} p={2} bg="red.900/20" borderLeft="3px solid" borderColor="red.400" borderRadius="md">
                                                    <Text fontSize="xs" color="red.300" fontWeight="semibold">
                                                        Failed: {msg.error}
                                                    </Text>
                                                </Box>
                                            )}
                                            {showResend && (
                                                <IconButton
                                                    aria-label="Resend message"
                                                    onClick={handleResend}
                                                    variant="ghost" size="xs"
                                                    color="var(--text-muted)"
                                                    _hover={{ color: 'var(--accent)', bg: 'var(--bg-hover)' }}
                                                    mt={1}>
                                                    <ResendIcon />
                                                </IconButton>
                                            )}
                                        </VStack>
                                    </HStack>
                                </Box>
                            );
                        })}
                        {isLoading && !messages.some(m => m.isStreaming) && (
                            <Box w="100%" bg="var(--bg-card)" py={compact ? 3 : 6} px={compact ? 3 : 4}>
                                <HStack maxW={maxW} mx={mx} align="flex-start" gap={compact ? 2 : 4}>
                                    <AssistantIcon />
                                    <HStack gap={2}>
                                        <Spinner size="sm" color="var(--text-assistant-label)" />
                                        <Text fontSize="sm" color="var(--text-tertiary)">Thinking...</Text>
                                    </HStack>
                                </HStack>
                            </Box>
                        )}
                        <div ref={messagesEndRef} />
                    </VStack>
                )}
            </Box>

            {statusBar}

            {routePending && (
                <Box w="100%" bg="rgba(102,126,234,0.08)" py={2} px={4}
                    borderTop="1px solid" borderColor="var(--border-secondary)">
                    <HStack maxW={maxW} mx={mx} gap={3} justify="space-between" flexWrap="wrap">
                        <HStack gap={2} flex={1} minW={0}>
                            <Box w="8px" h="8px" borderRadius="full"
                                bg="linear-gradient(135deg, #667eea, #764ba2)" flexShrink={0} />
                            <Text fontSize="xs" color="var(--text-secondary)" isTruncated>
                                {routePending.is_new
                                    ? `New conversation: "${routePending.title || 'Untitled'}"`
                                    : `Continue in "${routePending.title || 'Untitled'}"`}
                            </Text>
                        </HStack>
                        <HStack gap={2} flexShrink={0}>
                            <Box as="button" onClick={acceptRoute}
                                position="relative" overflow="hidden"
                                px={3} py={1} borderRadius="md" fontSize="xs" fontWeight="semibold"
                                color="white" cursor="pointer" _hover={{ opacity: 0.9 }}>
                                <Box position="absolute" inset={0} bg="var(--accent)" opacity={0.3} borderRadius="md" />
                                <Box position="absolute" top={0} left={0} bottom={0} borderRadius="md"
                                    bg="var(--accent)"
                                    style={{
                                        width: `${(routePending.countdown / 5) * 100}%`,
                                        transition: 'width 1s linear',
                                    }} />
                                <Text as="span" position="relative" zIndex={1}>
                                    Continue
                                </Text>
                            </Box>
                            <Box as="button" onClick={rejectRoute}
                                px={3} py={1} borderRadius="md" fontSize="xs" fontWeight="semibold"
                                bg="var(--bg-card)" color="var(--text-secondary)" cursor="pointer"
                                border="1px solid" borderColor="var(--border-secondary)"
                                _hover={{ bg: 'var(--bg-hover)' }}>
                                New Chat
                            </Box>
                        </HStack>
                    </HStack>
                </Box>
            )}

            {/* Input */}
            <Box w="100%" bg="var(--bg-page)" borderTop="1px solid" borderColor="var(--border-primary)"
                pt={2} pb={compact ? 2 : 4} px={compact ? 3 : 4}>
                <HStack maxW={maxW} mx={mx} mb={compact ? 1.5 : 2} justify="flex-start" gap={3}>
                    <NativeSelect.Root size="xs" w="auto">
                        <NativeSelect.Field
                            value={selectedAgent}
                            onChange={e => handleAgentChangeInternal(e.target.value)}
                            bg="var(--bg-input)" color="var(--text-tertiary)"
                            borderColor="var(--border-input)"
                            fontSize="xs" px={2} h={compact ? '24px' : '26px'} borderRadius="md">
                            {agents.map(a => (
                                <option key={a.name} value={a.name} style={{ background: 'var(--option-bg)' }}>
                                    {a.avatar ? `${a.avatar} ${a.name}` : a.name}
                                </option>
                            ))}
                        </NativeSelect.Field>
                    </NativeSelect.Root>
                    <NativeSelect.Root size="xs" w="auto">
                        <NativeSelect.Field
                            value={selectedProvider}
                            onChange={e => handleProviderChangeInternal(e.target.value)}
                            bg="var(--bg-input)" color="var(--text-tertiary)"
                            borderColor="var(--border-input)"
                            fontSize="xs" px={2} h={compact ? '24px' : '26px'} borderRadius="md">
                            <option value="auto" style={{ background: 'var(--option-bg)' }}>Auto</option>
                            {providers.map(p => (
                                <option key={p.name} value={p.name} style={{ background: 'var(--option-bg)' }}>
                                    {p.name}
                                </option>
                            ))}
                        </NativeSelect.Field>
                    </NativeSelect.Root>
                    <NativeSelect.Root size="xs" w="auto">
                        <NativeSelect.Field
                            value={thinkingMode}
                            onChange={e => { const v = e.target.value as ThinkingMode; setThinkingMode(v); localStorage.setItem('acai.thinking', v); }}
                            bg="var(--bg-input)"
                            color={thinkingMode === 'off' ? 'var(--text-tertiary)' : 'var(--accent)'}
                            borderColor="var(--border-input)"
                            fontSize="xs" px={2} h={compact ? '24px' : '26px'} borderRadius="md">
                            <option value="off" style={{ background: 'var(--option-bg)' }}>Think: Off</option>
                            <option value="native" style={{ background: 'var(--option-bg)' }}>Think: Native</option>
                            <option value="emulated" style={{ background: 'var(--option-bg)' }}>Think: Emulated</option>
                        </NativeSelect.Field>
                    </NativeSelect.Root>
                    {graphs.length > 1 && !initialGraph && (
                        <NativeSelect.Root size="xs" w="auto">
                            <NativeSelect.Field
                                value={selectedGraph}
                                onChange={e => { const v = e.target.value; setSelectedGraph(v); localStorage.setItem('acai.graph', v); }}
                                bg="var(--bg-input)"
                                color={selectedGraph === 'converse' ? 'var(--text-tertiary)' : 'var(--accent)'}
                                borderColor="var(--border-input)"
                                fontSize="xs" px={2} h={compact ? '24px' : '26px'} borderRadius="md"
                                title={graphs.find(g => g.kind === selectedGraph)?.description || ''}>
                                {graphs.map(g => (
                                    <option key={g.kind} value={g.kind} style={{ background: 'var(--option-bg)' }}>
                                        {g.label}
                                    </option>
                                ))}
                            </NativeSelect.Field>
                        </NativeSelect.Root>
                    )}
                    {contextStats && convIdRef.current && (
                        <ContextRing tokens={contextStats.estimated_tokens} maxTokens={contextStats.max_context} />
                    )}
                </HStack>
                <HStack maxW={maxW} mx={mx} gap={2} align="flex-end">
                    {compact ? (
                        <>
                            <Textarea
                                ref={textareaRef}
                                value={input}
                                onChange={handleChange}
                                onKeyDown={handleKeyDown}
                                placeholder={customPlaceholder || "Ask or instruct..."}
                                disabled={isLoading || externalDisabled}
                                rows={1} resize="none"
                                bg="var(--bg-card)" border="1px solid" borderColor="var(--border-secondary)"
                                _focus={{ borderColor: 'var(--accent)', boxShadow: 'none' }}
                                py={2} px={3} fontSize="sm" maxH="120px"
                                overflow="auto" flex={1}
                                color="var(--text-primary)" _placeholder={{ color: 'var(--text-muted)' }}
                                borderRadius="lg"
                            />
                            <IconButton
                                aria-label="Send"
                                onMouseDown={(e) => { e.preventDefault(); handleSend(); }}
                                disabled={isLoading || externalDisabled || !input.trim()}
                                colorScheme="green" size="sm" borderRadius="lg"
                                type="button" tabIndex={-1}>
                                <SendIcon size={18} />
                            </IconButton>
                        </>
                    ) : (
                        <>
                            <HStack
                                flex={1} bg="var(--bg-card)" borderRadius="xl"
                                border="1px solid" borderColor="var(--border-secondary)"
                                _focusWithin={{ borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' }}
                                align="flex-end" px={3}>
                                <Textarea
                                    ref={textareaRef}
                                    value={input}
                                    onChange={handleChange}
                                    onKeyDown={handleKeyDown}
                                    placeholder={customPlaceholder || "Describe what you want to build..."}
                                    disabled={isLoading || externalDisabled}
                                    rows={1} resize="none"
                                    border="none"
                                    _focus={{ outline: 'none', boxShadow: 'none' }}
                                    py={3} px={2} fontSize="md" maxH="200px"
                                    overflow="auto" bg="transparent" flex={1}
                                    color="var(--text-primary)" _placeholder={{ color: 'var(--text-muted)' }}
                                />
                            </HStack>
                            <IconButton
                                aria-label="Send message"
                                onMouseDown={(e) => { e.preventDefault(); handleSend(); }}
                                disabled={isLoading || externalDisabled || !input.trim()}
                                colorScheme="green" size="lg" borderRadius="xl"
                                h="50px" w="50px" flexShrink={0}
                                type="button" tabIndex={-1}>
                                <SendIcon />
                            </IconButton>
                        </>
                    )}
                </HStack>
            </Box>
        </Box>
    );
};

export default ChatPanel;
