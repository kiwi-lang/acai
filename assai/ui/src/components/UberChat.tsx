import { useState, useEffect, useRef, useCallback, KeyboardEvent, useLayoutEffect } from 'react';
import {
    Box, VStack, HStack, Text, Textarea, IconButton, Spinner,
    NativeSelect, Badge, Input,
} from '@chakra-ui/react';
import {
    uberConverse, getHistory, listConversations, updateConversation,
    listProviders, listAgents, getContextStats,
} from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentDef, AgentMessage, ConversationMeta, Provider } from '../services/types';
import Markdown from './Markdown';
import { ContextRing } from './ChatPanel';

/* ─── Icons ──────────────────────────────────────────────────────── */

const SendIcon = ({ size = 20 }: { size?: number }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
);

const EditIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7" />
        <path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z" />
    </svg>
);

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

const CloseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
    </svg>
);

const CheckIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="20 6 9 17 4 12" />
    </svg>
);

/* ─── Edit modal ─────────────────────────────────────────────────── */

interface EditModalProps {
    conv: ConversationMeta;
    onSave: (fields: { title?: string; description?: string; tags?: string[] }) => void;
    onClose: () => void;
}

const EditModal = ({ conv, onSave, onClose }: EditModalProps) => {
    const [title, setTitle] = useState(conv.title);
    const [description, setDescription] = useState(conv.description || '');
    const [tagsStr, setTagsStr] = useState((conv.tags || []).join(', '));

    const handleSave = () => {
        const tags = tagsStr.split(',').map(t => t.trim()).filter(Boolean);
        onSave({ title, description, tags });
    };

    return (
        <Box position="fixed" inset={0} bg="rgba(0,0,0,0.5)" zIndex={1000}
            display="flex" alignItems="center" justifyContent="center"
            onClick={onClose}>
            <Box bg="var(--bg-card)" p={6} borderRadius="lg" minW="420px" maxW="520px"
                border="1px solid" borderColor="var(--border-primary)"
                boxShadow="xl" onClick={e => e.stopPropagation()}>
                <HStack justify="space-between" mb={4}>
                    <Text fontWeight="semibold" color="var(--text-heading)">Edit Conversation</Text>
                    <IconButton aria-label="Close" variant="ghost" size="xs" onClick={onClose}>
                        <CloseIcon />
                    </IconButton>
                </HStack>

                <VStack gap={3} align="stretch">
                    <Box>
                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Title</Text>
                        <Input value={title} onChange={e => setTitle(e.target.value)}
                            bg="var(--bg-input)" borderColor="var(--border-input)"
                            color="var(--text-primary)" fontSize="sm" />
                    </Box>
                    <Box>
                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Description</Text>
                        <Textarea value={description} onChange={e => setDescription(e.target.value)}
                            bg="var(--bg-input)" borderColor="var(--border-input)"
                            color="var(--text-primary)" fontSize="sm" rows={3} resize="vertical" />
                    </Box>
                    <Box>
                        <Text fontSize="xs" color="var(--text-muted)" mb={1}>Tags (comma-separated)</Text>
                        <Input value={tagsStr} onChange={e => setTagsStr(e.target.value)}
                            bg="var(--bg-input)" borderColor="var(--border-input)"
                            color="var(--text-primary)" fontSize="sm"
                            placeholder="python, api, backend" />
                    </Box>
                </VStack>

                <HStack justify="flex-end" mt={5} gap={2}>
                    <IconButton aria-label="Cancel" variant="ghost" size="sm" onClick={onClose}
                        color="var(--text-muted)" _hover={{ color: 'var(--text-heading)' }}>
                        <CloseIcon />
                    </IconButton>
                    <IconButton aria-label="Save" size="sm" onClick={handleSave}
                        colorScheme="green">
                        <CheckIcon />
                    </IconButton>
                </HStack>
            </Box>
        </Box>
    );
};

/* ─── Sidebar ────────────────────────────────────────────────────── */

interface SidebarProps {
    conversations: ConversationMeta[];
    activeId: string | null;
    onSelect: (id: string) => void;
    onEdit: (conv: ConversationMeta) => void;
}

const ConversationSidebar = ({ conversations, activeId, onSelect, onEdit }: SidebarProps) => (
    <Box
        w="260px" flexShrink={0}
        borderRight="1px solid" borderColor="var(--border-primary)"
        display="flex" flexDirection="column"
        bg="var(--bg-page)"
    >
        <Box px={3} py={3} borderBottom="1px solid" borderColor="var(--border-primary)">
            <HStack>
                <Box w="24px" h="24px" borderRadius="sm"
                    display="flex" alignItems="center" justifyContent="center"
                    bg="linear-gradient(135deg, #667eea 0%, #764ba2 100%)"
                    color="white" fontWeight="bold" fontSize="2xs" flexShrink={0}>
                    U
                </Box>
                <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">
                    Uber Chat
                </Text>
            </HStack>
        </Box>
        <Box flex={1} overflowY="auto" px={2} py={2}>
            <VStack gap={1} align="stretch">
                {conversations.length === 0 && (
                    <Text fontSize="xs" color="var(--text-muted)" textAlign="center" py={4}>
                        No conversations yet
                    </Text>
                )}
                {conversations.map(c => (
                    <HStack
                        key={c.id} px={3} py={2} borderRadius="md" cursor="pointer"
                        bg={activeId === c.id ? 'var(--bg-active)' : 'transparent'}
                        _hover={{ bg: activeId === c.id ? 'var(--bg-active)' : 'var(--bg-hover)' }}
                        onClick={() => onSelect(c.id)}
                        role="group"
                    >
                        <VStack align="flex-start" flex={1} gap={0}>
                            <Text fontSize="sm" lineClamp={1}
                                color={activeId === c.id ? 'var(--text-heading)' : 'var(--text-tertiary)'}>
                                {c.title || 'Untitled'}
                            </Text>
                            {c.tags && c.tags.length > 0 && (
                                <HStack gap={1} flexWrap="wrap">
                                    {c.tags.slice(0, 3).map(tag => (
                                        <Badge key={tag} fontSize="2xs" px={1.5} py={0}
                                            borderRadius="full" bg="rgba(102,126,234,0.12)"
                                            color="var(--text-muted)" fontWeight="normal">
                                            {tag}
                                        </Badge>
                                    ))}
                                </HStack>
                            )}
                        </VStack>
                        <IconButton
                            aria-label="Edit"
                            onClick={(e) => { e.stopPropagation(); onEdit(c); }}
                            variant="ghost" size="xs"
                            color="var(--text-muted)"
                            _hover={{ color: 'var(--text-heading)', bg: 'transparent' }}
                        >
                            <EditIcon />
                        </IconButton>
                    </HStack>
                ))}
            </VStack>
        </Box>
    </Box>
);

/* ─── UberChat ───────────────────────────────────────────────────── */

const UberChat = () => {
    const [conversations, setConversations] = useState<ConversationMeta[]>([]);
    const [activeConv, setActiveConv] = useState<string | null>(null);
    const [messages, setMessages] = useState<AgentMessage[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [isRouting, setIsRouting] = useState(false);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [selectedProvider, setSelectedProvider] = useState('auto');
    const [selectedAgent, setSelectedAgent] = useState('default');
    const [editingConv, setEditingConv] = useState<ConversationMeta | null>(null);
    const [contextStats, setContextStats] = useState<{ estimated_tokens: number; max_context: number } | null>(null);

    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const shouldRestoreFocusRef = useRef(false);
    const eventSourceRef = useRef<EventSource | null>(null);

    const { joinConversation, leaveConversation } = useAgentSocket();

    const refreshConversations = useCallback(() => {
        listConversations().then(setConversations).catch(() => {});
    }, []);

    useEffect(() => {
        document.title = 'Uber Chat - ASSAI';
        listProviders().then(setProviders).catch(() => {});
        listAgents().then(setAgents).catch(() => {});
        refreshConversations();
    }, [refreshConversations]);

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

        es.addEventListener('done', () => {
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, isStreaming: false };
                }
                return copy;
            });
            setIsLoading(false);
            closeEventSource();
            refreshConversations();
        });

        es.addEventListener('error', (e: MessageEvent) => {
            let errorMsg = 'Stream error';
            try {
                const data = JSON.parse(e.data);
                errorMsg = data.error || errorMsg;
            } catch { /* raw EventSource error */ }
            setMessages(prev => {
                const copy = [...prev];
                const last = copy[copy.length - 1];
                if (last && last.isStreaming) {
                    copy[copy.length - 1] = { ...last, content: `Error: ${errorMsg}`, isStreaming: false };
                }
                return copy;
            });
            setIsLoading(false);
            closeEventSource();
        });

        es.onerror = () => {
            setIsLoading(false);
            closeEventSource();
        };
    }, [closeEventSource, refreshConversations]);

    useEffect(() => {
        return () => closeEventSource();
    }, [closeEventSource]);

    const loadConversation = useCallback((convId: string) => {
        closeEventSource();
        setMessages([]);
        setIsLoading(false);

        if (activeConv) leaveConversation(activeConv);
        setActiveConv(convId);
        joinConversation(convId);

        getHistory(convId).then(resp => {
            setMessages(resp.messages);
            if (resp.streaming) {
                setIsLoading(true);
                setMessages(prev => [
                    ...prev,
                    { role: 'assistant', content: resp.streaming!.partial, isStreaming: true, taskId: resp.streaming!.task_id },
                ]);
                openEventSource(convId);
            }
        }).catch(() => {});
    }, [activeConv, closeEventSource, joinConversation, leaveConversation, openEventSource]);

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
        if (activeConv) {
            getContextStats(activeConv).then(setContextStats).catch(() => {});
        }
    }, [activeConv]);

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

    const handleSend = async () => {
        const text = input.trim();
        if (!text || isLoading || isRouting) return;

        if (document.activeElement === textareaRef.current) {
            shouldRestoreFocusRef.current = true;
        }

        setInput('');
        if (textareaRef.current) textareaRef.current.style.height = 'auto';
        setIsRouting(true);

        try {
            const resp = await uberConverse(text, activeConv || '', selectedProvider, selectedAgent);
            setIsRouting(false);

            if (resp.conversation !== activeConv) {
                if (activeConv) leaveConversation(activeConv);
                setActiveConv(resp.conversation);
                joinConversation(resp.conversation);

                const historyResp = await getHistory(resp.conversation);
                setMessages(historyResp.messages);
            } else {
                setMessages(prev => [...prev, { role: 'user', content: text }]);
            }

            setIsLoading(true);
            setMessages(prev => [
                ...prev,
                { role: 'assistant', content: '', isStreaming: true, taskId: resp.task_id },
            ]);
            openEventSource(resp.conversation);

            if (resp.is_new) refreshConversations();
        } catch (err) {
            setIsRouting(false);
            const msg = err instanceof Error ? err.message : 'Request failed';
            setMessages(prev => [...prev,
                { role: 'user', content: text },
                { role: 'assistant', content: `Error: ${msg}` },
            ]);
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
        e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px';
    };

    const handleEditSave = async (fields: { title?: string; description?: string; tags?: string[] }) => {
        if (!editingConv) return;
        await updateConversation(editingConv.id, fields).catch(() => {});
        setEditingConv(null);
        refreshConversations();
    };

    /* ── Render ── */

    const maxW = '48rem';
    const mx = 'auto';

    return (
        <Box display="flex" h="100vh" w="100%" bg="var(--bg-page)" overflow="hidden">
            <ConversationSidebar
                conversations={conversations}
                activeId={activeConv}
                onSelect={loadConversation}
                onEdit={setEditingConv}
            />

            <Box flex={1} display="flex" flexDirection="column" overflow="hidden">
                {/* Messages */}
                <Box flex={1} overflowY="auto" w="100%" minH={0}>
                    {!activeConv && messages.length === 0 ? (
                        <VStack flex={1} justify="center" align="center" p={8} gap={6} minH="60vh">
                            <Box w="64px" h="64px" borderRadius="xl"
                                display="flex" alignItems="center" justifyContent="center"
                                fontSize="2xl" color="white" fontWeight="bold"
                                bg="linear-gradient(135deg, #667eea 0%, #764ba2 100%)">
                                U
                            </Box>
                            <VStack gap={2}>
                                <Text fontSize="lg" fontWeight="semibold" color="var(--text-heading)">
                                    Uber Chat
                                </Text>
                                <Text fontSize="md" color="var(--text-tertiary)" textAlign="center" maxW="md">
                                    Type a message and it will be automatically routed to the right
                                    conversation — or a new one will be created.
                                </Text>
                            </VStack>
                        </VStack>
                    ) : messages.length === 0 && activeConv ? (
                        <VStack flex={1} justify="center" align="center" p={8} gap={4} minH="40vh">
                            <Text fontSize="md" color="var(--text-tertiary)" textAlign="center">
                                Empty conversation. Send a message to get started.
                            </Text>
                        </VStack>
                    ) : (
                        <VStack gap={0} w="100%">
                            {messages.map((msg, i) => {
                                if (msg.role === 'tool_call' || msg.role === 'tool_result') return null;
                                return (
                                    <Box key={i} w="100%"
                                        bg={msg.role === 'user' ? 'transparent' : 'var(--bg-card)'}
                                        py={6} px={4}>
                                        <HStack maxW={maxW} mx={mx} align="flex-start" gap={4}>
                                            {msg.role === 'user' ? <UserIcon /> : <AssistantIcon />}
                                            <VStack align="flex-start" flex={1} gap={1}>
                                                <Text fontWeight="semibold" fontSize="sm"
                                                    color={msg.role === 'user' ? 'var(--text-user-label)' : 'var(--text-assistant-label)'}>
                                                    {msg.role === 'user' ? 'You' : 'Agent'}
                                                </Text>
                                                <Markdown content={msg.content} fontSize="md" />
                                                {msg.isStreaming && (
                                                    <Box as="span" display="inline-block" w="2px" h="1em"
                                                        bg="var(--cursor-blink)" ml={0.5}
                                                        animation="blink 1s step-start infinite" />
                                                )}
                                            </VStack>
                                        </HStack>
                                    </Box>
                                );
                            })}
                            {isLoading && !messages.some(m => m.isStreaming) && (
                                <Box w="100%" bg="var(--bg-card)" py={6} px={4}>
                                    <HStack maxW={maxW} mx={mx} align="flex-start" gap={4}>
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

                {/* Routing indicator */}
                {isRouting && (
                    <Box w="100%" bg="rgba(102,126,234,0.08)" py={2} px={4}>
                        <HStack maxW={maxW} mx={mx} gap={2}>
                            <Spinner size="xs" color="purple.400" />
                            <Text fontSize="xs" color="var(--text-secondary)">
                                Finding the right conversation...
                            </Text>
                        </HStack>
                    </Box>
                )}

                {/* Input */}
                <Box w="100%" bg="var(--bg-page)" borderTop="1px solid" borderColor="var(--border-primary)"
                    pt={2} pb={4} px={4}>
                    <HStack maxW={maxW} mx={mx} mb={2} justify="flex-start" gap={3}>
                        <NativeSelect.Root size="xs" w="auto">
                            <NativeSelect.Field
                                value={selectedAgent}
                                onChange={e => setSelectedAgent(e.target.value)}
                                bg="var(--bg-input)" color="var(--text-tertiary)"
                                borderColor="var(--border-input)"
                                fontSize="xs" px={2} h="26px" borderRadius="md">
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
                                onChange={e => setSelectedProvider(e.target.value)}
                                bg="var(--bg-input)" color="var(--text-tertiary)"
                                borderColor="var(--border-input)"
                                fontSize="xs" px={2} h="26px" borderRadius="md">
                                <option value="auto" style={{ background: 'var(--option-bg)' }}>Auto</option>
                                {providers.map(p => (
                                    <option key={p.name} value={p.name} style={{ background: 'var(--option-bg)' }}>
                                        {p.name}
                                    </option>
                                ))}
                            </NativeSelect.Field>
                        </NativeSelect.Root>
                        {contextStats && activeConv && (
                            <ContextRing tokens={contextStats.estimated_tokens} maxTokens={contextStats.max_context} />
                        )}
                    </HStack>
                    <HStack maxW={maxW} mx={mx} gap={2} align="flex-end">
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
                                placeholder="Ask anything — conversations are picked automatically..."
                                disabled={isLoading || isRouting}
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
                            disabled={isLoading || isRouting || !input.trim()}
                            colorScheme="purple" size="lg" borderRadius="xl"
                            h="50px" w="50px" flexShrink={0}
                            type="button" tabIndex={-1}>
                            <SendIcon />
                        </IconButton>
                    </HStack>
                </Box>
            </Box>

            {editingConv && (
                <EditModal
                    conv={editingConv}
                    onSave={handleEditSave}
                    onClose={() => setEditingConv(null)}
                />
            )}
        </Box>
    );
};

export default UberChat;
