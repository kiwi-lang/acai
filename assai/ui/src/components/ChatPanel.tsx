import { useState, useEffect, useRef, useCallback, KeyboardEvent, useLayoutEffect } from 'react';
import { Box, VStack, HStack, Text, Textarea, IconButton, Spinner, NativeSelect } from '@chakra-ui/react';
import { converse, getHistory, listProviders, listAgents, checkInflight, getContextStats } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentDef, AgentMessage, Provider } from '../services/types';
import Markdown from './Markdown';

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
}: ChatPanelProps) => {
    const fallbackAgent = project ? (refinerAgent ?? 'refiner') : 'default';
    const resolvedInitialAgent = initialAgent ?? fallbackAgent;

    const [messages, setMessages] = useState<AgentMessage[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [selectedProvider, setSelectedProvider] = useState(initialProvider);
    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [selectedAgent, setSelectedAgent] = useState(resolvedInitialAgent);
    const [contextStats, setContextStats] = useState<{ estimated_tokens: number; max_context: number } | null>(null);

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const shouldRestoreFocusRef = useRef(false);
    const activeTaskRef = useRef<string | null>(null);
    const eventSourceRef = useRef<EventSource | null>(null);
    const convIdRef = useRef<string | null>(conversationId);
    const justCreatedRef = useRef(false);

    const initialProviderRef = useRef(initialProvider);
    const initialAgentRef = useRef(resolvedInitialAgent);
    initialProviderRef.current = initialProvider;
    initialAgentRef.current = resolvedInitialAgent;

    useEffect(() => {
        if (conversationId) return;
        const fb = project ? (refinerAgent ?? 'refiner') : 'default';
        const next = initialAgent ?? fb;
        initialAgentRef.current = next;
        setSelectedAgent(next);
    }, [conversationId, project, refinerAgent, initialAgent]);

    const onProviderChangeRef = useRef(onProviderChange);
    const onAgentChangeRef = useRef(onAgentChange);
    onProviderChangeRef.current = onProviderChange;
    onAgentChangeRef.current = onAgentChange;

    const { joinConversation, leaveConversation } = useAgentSocket();

    useEffect(() => {
        listProviders().then(setProviders).catch(() => {});
        listAgents().then(setAgents).catch(() => {});
    }, []);

    const closeEventSource = useCallback(() => {
        if (eventSourceRef.current) {
            eventSourceRef.current.close();
            eventSourceRef.current = null;
        }
    }, []);

    const openEventSource = useCallback((convId: string) => {
        closeEventSource();
        const es = new EventSource(`/api/agent/stream/${convId}`);
        eventSourceRef.current = es;

        es.addEventListener('token', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, content: last.content + (data.token || '') };
                }
                return copy;
            });
        });

        es.addEventListener('tool_start', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            setMessages(prev => [...prev, {
                role: 'tool_call' as const,
                content: JSON.stringify({ tool: data.tool_name, args: data.args }, null, 2),
                name: data.tool_name,
            }]);
        });

        es.addEventListener('tool_end', (e: MessageEvent) => {
            const data = JSON.parse(e.data);
            setMessages(prev => [...prev, {
                role: 'tool_result' as const,
                content: data.result_preview || '(done)',
                name: data.tool_name,
            }]);
        });

        es.addEventListener('done', () => {
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, isStreaming: false };
                }
                return copy;
            });
            activeTaskRef.current = null;
            setIsLoading(false);
            closeEventSource();
        });

        es.addEventListener('error', (e: MessageEvent) => {
            let errorMsg = 'Stream error';
            try {
                const data = JSON.parse(e.data);
                errorMsg = data.error || errorMsg;
            } catch { /* raw error event from EventSource */ }
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, content: `Error: ${errorMsg}`, isStreaming: false };
                }
                return copy;
            });
            activeTaskRef.current = null;
            setIsLoading(false);
            closeEventSource();
        });

        es.onerror = () => {
            activeTaskRef.current = null;
            setIsLoading(false);
            closeEventSource();
        };
    }, [closeEventSource]);

    useEffect(() => {
        return () => closeEventSource();
    }, [closeEventSource]);

    useEffect(() => {
        convIdRef.current = conversationId;

        if (justCreatedRef.current) {
            justCreatedRef.current = false;
            return;
        }

        setSelectedProvider(initialProviderRef.current);
        setSelectedAgent(initialAgentRef.current);
        setMessages([]);
        setIsLoading(false);
        activeTaskRef.current = null;
        closeEventSource();

        if (!conversationId) return;

        getHistory(conversationId).then(resp => {
            setMessages(resp.messages);
            if (resp.streaming) {
                const tid = resp.streaming.task_id;
                activeTaskRef.current = tid;
                setIsLoading(true);
                setMessages(prev => [
                    ...prev,
                    { role: 'assistant', content: resp.streaming!.partial, isStreaming: true, taskId: tid },
                ]);
                openEventSource(conversationId);
            }
        }).catch(() => {});
    }, [conversationId, closeEventSource, openEventSource]);

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
        if (shouldRestoreFocusRef.current && textareaRef.current && !isLoading) {
            const id = setTimeout(() => {
                textareaRef.current?.focus();
                shouldRestoreFocusRef.current = false;
            }, 50);
            return () => clearTimeout(id);
        }
    }, [input, isLoading]);

    /* ── Actions ── */

    const handleProviderChangeInternal = useCallback((value: string) => {
        setSelectedProvider(value);
        onProviderChangeRef.current?.(value);
    }, []);

    const handleAgentChangeInternal = useCallback((value: string) => {
        setSelectedAgent(value);
        onAgentChangeRef.current?.(value);
    }, []);

    const handleResend = useCallback(async () => {
        const cid = convIdRef.current;
        if (!cid || isLoading) return;
        const lastUserMsg = [...messages].reverse().find(m => m.role === 'user');
        if (!lastUserMsg) return;

        const inflightResp = await checkInflight(cid).catch(() => ({ inflight: false }));
        if (inflightResp.inflight) return;

        setIsLoading(true);
        try {
            const resp = await converse(lastUserMsg.content, cid, project || '', '', selectedProvider, selectedAgent);
            activeTaskRef.current = resp.task_id;
            setMessages(prev => [
                ...prev,
                { role: 'assistant', content: '', isStreaming: true, taskId: resp.task_id },
            ]);
            openEventSource(cid);
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Request failed';
            setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${msg}` }]);
            setIsLoading(false);
        }
    }, [isLoading, messages, project, selectedProvider, selectedAgent, openEventSource]);

    const handleSend = async () => {
        const text = input.trim();
        if (!text || isLoading) return;

        if (document.activeElement === textareaRef.current) {
            shouldRestoreFocusRef.current = true;
        }

        setInput('');
        if (textareaRef.current) textareaRef.current.style.height = 'auto';

        setMessages(prev => [...prev, { role: 'user', content: text }]);
        setIsLoading(true);

        try {
            const resp = await converse(text, convIdRef.current || '', project || '', '', selectedProvider, selectedAgent);
            activeTaskRef.current = resp.task_id;
            convIdRef.current = resp.conversation;

            joinConversation(resp.conversation);

            if (!conversationId) {
                justCreatedRef.current = true;
                onConversationCreated?.(resp.conversation);
            }

            setMessages(prev => [
                ...prev,
                { role: 'assistant', content: '', isStreaming: true, taskId: resp.task_id },
            ]);

            openEventSource(resp.conversation);
        } catch (err) {
            const msg = err instanceof Error ? err.message : 'Request failed';
            setMessages(prev => [...prev, { role: 'assistant', content: `Error: ${msg}` }]);
            setIsLoading(false);
        }
    };

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
        <Box flex={1} display="flex" flexDirection="column" overflow="hidden">
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

                            if (msg.role === 'tool_call') {
                                const result = messages.slice(i + 1)
                                    .find(m => m.role === 'tool_result' && m.name === msg.name);
                                return (
                                    <Box key={i} w="100%" bg="var(--bg-card)" py={compact ? 2 : 3} px={compact ? 3 : 4}>
                                        <HStack maxW={maxW} mx={mx} align="flex-start" gap={compact ? 2 : 4}>
                                            <AssistantIcon />
                                            <VStack align="flex-start" flex={1} gap={1}>
                                                <Text fontWeight="semibold" fontSize={compact ? 'xs' : 'sm'} color="var(--text-assistant-label)">
                                                    Agent
                                                </Text>
                                                <ToolCallCard callMsg={msg} resultMsg={result} />
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
                                            <Markdown content={msg.content} fontSize={compact ? 'sm' : 'md'} />
                                            {msg.isStreaming && (
                                                <Box as="span" display="inline-block" w="2px" h="1em"
                                                    bg="var(--cursor-blink)" ml={0.5}
                                                    animation="blink 1s step-start infinite" />
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
                                placeholder="Ask or instruct..."
                                disabled={isLoading}
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
                                disabled={isLoading || !input.trim()}
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
                                    placeholder="Describe what you want to build..."
                                    disabled={isLoading}
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
                                disabled={isLoading || !input.trim()}
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
