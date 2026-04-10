import { useState, useEffect, useRef, useCallback, KeyboardEvent, useLayoutEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { Box, VStack, HStack, Text, Textarea, IconButton, Spinner, NativeSelect } from '@chakra-ui/react';
import {
    converse, getHistory, listConversations, deleteConversation,
    listProviders, listAgents, updateConversation,
} from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentDef, AgentMessage, StreamChunk, ConversationMeta, Provider } from '../services/types';
import Markdown from './Markdown';

const SendIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
);

const PlusIcon = () => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <line x1="12" y1="5" x2="12" y2="19" /><line x1="5" y1="12" x2="19" y2="12" />
    </svg>
);

const TrashIcon = () => (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14H6L5 6" />
        <path d="M10 11v6" /><path d="M14 11v6" /><path d="M9 6V4h6v2" />
    </svg>
);

const UserIcon = () => (
    <Box
        w="32px" h="32px" bg="purple.500" borderRadius="sm"
        display="flex" alignItems="center" justifyContent="center"
        color="var(--text-inverse)" fontWeight="bold" fontSize="sm"
    >
        U
    </Box>
);

const AssistantIcon = () => (
    <Box
        w="32px" h="32px" bg="var(--bg-brand-icon)" borderRadius="sm"
        display="flex" alignItems="center" justifyContent="center"
        color="var(--text-inverse)" fontWeight="bold" fontSize="sm"
    >
        AI
    </Box>
);

const Home = () => {
    const { convId: urlConvId } = useParams<{ convId?: string }>();
    const navigate = useNavigate();

    const [conversations, setConversations] = useState<ConversationMeta[]>([]);
    const [activeConv, setActiveConv] = useState<string | null>(urlConvId || null);
    const [messages, setMessages] = useState<AgentMessage[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [selectedProvider, setSelectedProvider] = useState('auto');
    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [selectedAgent, setSelectedAgent] = useState('default');
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const shouldRestoreFocusRef = useRef(false);
    const activeTaskRef = useRef<string | null>(null);
    const chunkBufferRef = useRef<StreamChunk[]>([]);

    const { onChunk, onStreamEnd, onStreamError } = useAgentSocket();

    const refreshList = useCallback(() => {
        listConversations().then(setConversations).catch(() => {});
    }, []);

    const flushBuffer = useCallback((taskId: string) => {
        const buffered = chunkBufferRef.current.filter(c => c.task_id === taskId);
        chunkBufferRef.current = [];
        if (buffered.length === 0) return;
        setMessages(prev => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.isStreaming && last.taskId === taskId) {
                const extra = buffered.map(c => c.token).join('');
                copy[copy.length - 1] = { ...last, content: last.content + extra };
            }
            return copy;
        });
    }, []);

    const loadConversation = useCallback((id: string) => {
        setMessages([]);
        setIsLoading(false);
        activeTaskRef.current = null;
        chunkBufferRef.current = [];

        getHistory(id).then(resp => {
            setMessages(resp.messages);
            if (resp.streaming) {
                const tid = resp.streaming.task_id;
                activeTaskRef.current = tid;
                setIsLoading(true);
                setMessages(prev => [
                    ...prev,
                    {
                        role: 'assistant',
                        content: resp.streaming!.partial,
                        isStreaming: true,
                        taskId: tid,
                    },
                ]);
                flushBuffer(tid);
            }
        }).catch(() => {});
    }, [flushBuffer]);

    useEffect(() => {
        document.title = 'Conversations - ASSAI';
        refreshList();
        listProviders().then(setProviders).catch(() => {});
        listAgents().then(setAgents).catch(() => {});
    }, [refreshList]);

    useEffect(() => {
        const id = urlConvId || null;
        setActiveConv(id);
        if (id) {
            loadConversation(id);
            const conv = conversations.find(c => c.id === id);
            setSelectedProvider(conv?.provider || 'auto');
            setSelectedAgent(conv?.agent || 'default');
        } else {
            setMessages([]);
            setIsLoading(false);
            activeTaskRef.current = null;
            setSelectedProvider('auto');
            setSelectedAgent('default');
        }
    }, [urlConvId, loadConversation, conversations]);

    const selectConversation = useCallback((id: string) => {
        navigate(`/conversations/${id}`);
    }, [navigate]);

    const handleNewConversation = useCallback(() => {
        navigate('/');
        textareaRef.current?.focus();
    }, [navigate]);

    const handleDeleteConversation = useCallback((id: string) => {
        deleteConversation(id).then(() => {
            if (activeConv === id) {
                navigate('/');
            }
            refreshList();
        }).catch(() => {});
    }, [activeConv, refreshList, navigate]);

    useEffect(() => {
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }, [messages]);

    useLayoutEffect(() => {
        if (shouldRestoreFocusRef.current && textareaRef.current && !isLoading) {
            const id = setTimeout(() => {
                textareaRef.current?.focus();
                shouldRestoreFocusRef.current = false;
            }, 50);
            return () => clearTimeout(id);
        }
    }, [input, isLoading]);

    const handleChunk = useCallback((chunk: StreamChunk) => {
        if (activeTaskRef.current === null) {
            chunkBufferRef.current.push(chunk);
            return;
        }
        if (chunk.task_id !== activeTaskRef.current) return;
        setMessages(prev => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.isStreaming && last.taskId === chunk.task_id) {
                copy[copy.length - 1] = { ...last, content: last.content + chunk.token };
            }
            return copy;
        });
    }, []);

    const handleStreamEnd = useCallback((data: { task_id: string }) => {
        if (data.task_id !== activeTaskRef.current) return;
        setMessages(prev => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.isStreaming && last.taskId === data.task_id) {
                copy[copy.length - 1] = { ...last, isStreaming: false };
            }
            return copy;
        });
        activeTaskRef.current = null;
        setIsLoading(false);
    }, []);

    const handleStreamError = useCallback((data: { task_id: string; error: string }) => {
        if (data.task_id !== activeTaskRef.current) return;
        setMessages(prev => {
            const copy = [...prev];
            const last = copy[copy.length - 1];
            if (last && last.isStreaming && last.taskId === data.task_id) {
                copy[copy.length - 1] = {
                    ...last,
                    content: `⚠ Error: ${data.error}`,
                    isStreaming: false,
                };
            }
            return copy;
        });
        activeTaskRef.current = null;
        setIsLoading(false);
    }, []);

    useEffect(() => {
        const unsub1 = onChunk(handleChunk);
        const unsub2 = onStreamEnd(handleStreamEnd);
        const unsub3 = onStreamError(handleStreamError);
        return () => { unsub1(); unsub2(); unsub3(); };
    }, [onChunk, onStreamEnd, onStreamError, handleChunk, handleStreamEnd, handleStreamError]);

    const handleProviderChange = useCallback((value: string) => {
        setSelectedProvider(value);
        if (activeConv) {
            updateConversation(activeConv, { provider: value }).catch(() => {});
        }
    }, [activeConv]);

    const handleAgentChange = useCallback((value: string) => {
        setSelectedAgent(value);
        if (activeConv) {
            updateConversation(activeConv, { agent: value }).catch(() => {});
        }
    }, [activeConv]);

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
            const resp = await converse(text, activeConv || '', '', '', selectedProvider, selectedAgent);
            activeTaskRef.current = resp.task_id;

            if (!activeConv) {
                setActiveConv(resp.conversation);
                navigate(`/conversations/${resp.conversation}`, { replace: true });
                refreshList();
            }

            setMessages(prev => [
                ...prev,
                { role: 'assistant', content: '', isStreaming: true, taskId: resp.task_id },
            ]);
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
        e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px';
    };

    return (
        <Box display="flex" h="100vh" w="100%" bg="var(--bg-page)" overflow="hidden">
            {/* Sidebar */}
            <Box
                w="260px" flexShrink={0}
                borderRight="1px solid" borderColor="var(--border-primary)"
                display="flex" flexDirection="column"
                bg="var(--bg-page)"
            >
                <Box px={3} py={3} borderBottom="1px solid" borderColor="var(--border-primary)">
                    <HStack justify="space-between">
                        <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">
                            Conversations
                        </Text>
                        <IconButton
                            aria-label="New conversation"
                            onClick={handleNewConversation}
                            variant="ghost" size="xs" color="var(--text-tertiary)"
                            _hover={{ color: 'var(--text-heading)', bg: 'var(--bg-hover)' }}
                        >
                            <PlusIcon />
                        </IconButton>
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
                                key={c.id}
                                px={3} py={2}
                                borderRadius="md"
                                cursor="pointer"
                                bg={activeConv === c.id ? 'var(--bg-active)' : 'transparent'}
                                _hover={{ bg: activeConv === c.id ? 'var(--bg-active)' : 'var(--bg-hover)' }}
                                onClick={() => selectConversation(c.id)}
                                role="group"
                            >
                                <Text
                                    flex={1}
                                    fontSize="sm"
                                    color={activeConv === c.id ? 'var(--text-heading)' : 'var(--text-tertiary)'}
                                    lineClamp={1}
                                >
                                    {c.title || 'Untitled'}
                                </Text>
                                <IconButton
                                    aria-label="Delete"
                                    onClick={(e) => { e.stopPropagation(); handleDeleteConversation(c.id); }}
                                    variant="ghost" size="xs"
                                    color="var(--text-muted)"
                                    opacity={0}
                                    _groupHover={{ opacity: 1 }}
                                    _hover={{ color: 'red.400', bg: 'transparent' }}
                                >
                                    <TrashIcon />
                                </IconButton>
                            </HStack>
                        ))}
                    </VStack>
                </Box>
            </Box>

            {/* Chat area */}
            <Box flex={1} display="flex" flexDirection="column" overflow="hidden">
                {/* Messages */}
                <Box flex={1} overflowY="auto" w="100%" minH={0}>
                    {messages.length === 0 ? (
                        <VStack flex={1} justify="center" align="center" p={8} gap={6} minH="60vh">
                            <Box
                                w="64px" h="64px" bg="var(--bg-brand-icon)" borderRadius="xl"
                                display="flex" alignItems="center" justifyContent="center"
                                fontSize="2xl" color="var(--text-inverse)" fontWeight="bold"
                            >
                                AI
                            </Box>
                            <VStack gap={2} textAlign="center">
                                <Text fontSize="2xl" fontWeight="semibold" color="var(--text-heading)">
                                    ASSAI Agent
                                </Text>
                                <Text fontSize="md" color="var(--text-tertiary)" maxW="md">
                                    {activeConv
                                        ? 'This conversation is empty. Send a message to start.'
                                        : 'Start a new conversation or pick one from the sidebar.'}
                                </Text>
                            </VStack>
                            {!activeConv && (
                                <VStack gap={2} w="100%" maxW="2xl" mt={4}>
                                    <Text fontSize="sm" fontWeight="semibold" color="var(--text-secondary)">Try:</Text>
                                    {[
                                        'Describe the project architecture',
                                        'Add a new REST endpoint for user profiles',
                                        'What does the current spec say about testing?',
                                    ].map(example => (
                                        <Box
                                            key={example} p={3} bg="var(--bg-card)" borderRadius="lg" w="100%"
                                            fontSize="sm" color="var(--text-secondary)" cursor="pointer"
                                            _hover={{ bg: 'var(--bg-hover)' }}
                                            onClick={() => { setInput(example); textareaRef.current?.focus(); }}
                                        >
                                            {example}
                                        </Box>
                                    ))}
                                </VStack>
                            )}
                        </VStack>
                    ) : (
                        <VStack gap={0} w="100%">
                            {messages.map((msg, i) => (
                                <Box key={i} w="100%" bg={msg.role === 'user' ? 'transparent' : 'var(--bg-card)'} py={6} px={4}>
                                    <HStack maxW="48rem" mx="auto" align="flex-start" gap={4}>
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
                            ))}
                            {isLoading && !messages.some(m => m.isStreaming) && (
                                <Box w="100%" bg="var(--bg-card)" py={6} px={4}>
                                    <HStack maxW="48rem" mx="auto" align="flex-start" gap={4}>
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
                <Box w="100%" bg="var(--bg-page)" borderTop="1px solid" borderColor="var(--border-primary)" pt={2} pb={4} px={4}>
                    <HStack maxW="48rem" mx="auto" mb={2} justify="flex-start" gap={3}>
                        <NativeSelect.Root size="xs" w="auto">
                            <NativeSelect.Field
                                value={selectedAgent}
                                onChange={e => handleAgentChange(e.target.value)}
                                bg="var(--bg-input)"
                                color="var(--text-tertiary)"
                                borderColor="var(--border-input)"
                                fontSize="xs"
                                px={2}
                                h="26px"
                                borderRadius="md"
                            >
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
                                onChange={e => handleProviderChange(e.target.value)}
                                bg="var(--bg-input)"
                                color="var(--text-tertiary)"
                                borderColor="var(--border-input)"
                                fontSize="xs"
                                px={2}
                                h="26px"
                                borderRadius="md"
                            >
                                <option value="auto" style={{ background: 'var(--option-bg)' }}>
                                    Auto
                                </option>
                                {providers.map(p => (
                                    <option key={p.name} value={p.name} style={{ background: 'var(--option-bg)' }}>
                                        {p.name}
                                    </option>
                                ))}
                            </NativeSelect.Field>
                        </NativeSelect.Root>
                    </HStack>
                    <HStack maxW="48rem" mx="auto" gap={2} align="flex-end">
                        <HStack
                            flex={1} bg="var(--bg-card)" borderRadius="xl" border="1px solid" borderColor="var(--border-secondary)"
                            _focusWithin={{ borderColor: 'var(--accent)', boxShadow: '0 0 0 1px var(--accent)' }}
                            align="flex-end" px={3}
                        >
                            <Textarea
                                ref={textareaRef}
                                value={input}
                                onChange={handleChange}
                                onKeyDown={handleKeyDown}
                                placeholder="Describe what you want to build..."
                                disabled={isLoading}
                                rows={1}
                                resize="none"
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
                            colorScheme="green"
                            size="lg"
                            borderRadius="xl"
                            h="50px" w="50px"
                            flexShrink={0}
                            type="button"
                            tabIndex={-1}
                        >
                            <SendIcon />
                        </IconButton>
                    </HStack>
                </Box>
            </Box>
        </Box>
    );
};

export default Home;
