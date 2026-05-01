import { useState, useEffect, useRef, useCallback, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, VStack, HStack, Text, Textarea, IconButton, Spinner, NativeSelect, Image, Button } from '@chakra-ui/react';
import { uberConverse, listProviders, listAgents, type SSEStream } from '../services/api';
import type { AgentDef, Provider } from '../services/types';

const SendIcon = ({ size = 20 }: { size?: number }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
);

type ThinkingMode = 'off' | 'native';

interface RoutePending {
    conversation: string;
    is_new: boolean;
    title: string;
    message: string;
    countdown: number;
}

const Home = () => {
    const navigate = useNavigate();
    const [input, setInput] = useState('');
    const [isRouting, setIsRouting] = useState(false);
    const [routePending, setRoutePending] = useState<RoutePending | null>(null);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [selectedProvider, setSelectedProvider] = useState(
        () => localStorage.getItem('acai.provider') || 'auto',
    );
    const [selectedAgent, setSelectedAgent] = useState(
        () => localStorage.getItem('acai.agent') || 'default',
    );
    const [thinkingEnabled, setThinkingEnabled] = useState<boolean>(
        () => {
            const stored = localStorage.getItem('acai.thinking');
            return stored ? stored === 'native' : true;
        },
    );
    const thinkingMode: ThinkingMode = thinkingEnabled ? 'native' : 'off';

    const currentProvider = providers.find(p =>
        selectedProvider === 'auto' ? p.active : p.name === selectedProvider,
    );
    const canThink = currentProvider?.supports_thinking ?? false;
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const routeTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
    const streamRef = useRef<SSEStream | null>(null);

    useEffect(() => {
        document.title = 'Açaí';
        listProviders().then(setProviders).catch(() => {});
        listAgents().then(setAgents).catch(() => {});
        return () => { streamRef.current?.close(); };
    }, []);

    const clearRouteTimer = useCallback(() => {
        if (routeTimerRef.current) {
            clearInterval(routeTimerRef.current);
            routeTimerRef.current = null;
        }
    }, []);

    const goToConversation = useCallback((convId: string, text: string) => {
        clearRouteTimer();
        setRoutePending(null);
        navigate(`/conversations/${convId}`, {
            state: { pendingMessage: text, provider: selectedProvider, agent: selectedAgent, thinkingMode },
        });
    }, [navigate, selectedProvider, selectedAgent, thinkingMode, clearRouteTimer]);

    const acceptRoute = useCallback(() => {
        if (!routePending) return;
        goToConversation(routePending.conversation, routePending.message);
    }, [routePending, goToConversation]);

    const rejectRoute = useCallback(() => {
        if (!routePending) return;
        clearRouteTimer();
        setRoutePending(null);
        setIsRouting(false);
        navigate('/conversations', {
            state: { pendingMessage: routePending.message, provider: selectedProvider, agent: selectedAgent, thinkingMode },
        });
    }, [routePending, navigate, selectedProvider, selectedAgent, thinkingMode, clearRouteTimer]);

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

    const handleSend = async () => {
        const text = input.trim();
        if (!text || isRouting) return;

        setIsRouting(true);
        try {
            streamRef.current?.close();
            const resp = await uberConverse(text, '', selectedAgent);
            const stream = resp.stream;
            streamRef.current = stream;

            stream.addEventListener('route', (e: MessageEvent) => {
                const data = JSON.parse(e.data);
                stream.close();
                streamRef.current = null;
                if (data.is_new) {
                    goToConversation(data.conversation, text);
                    return;
                }
                setRoutePending({
                    conversation: data.conversation,
                    is_new: false,
                    title: data.title || '',
                    message: text,
                    countdown: 5,
                });
            });
            stream.addEventListener('error', (e: MessageEvent) => {
                let errorMsg = 'Routing failed';
                try {
                    const data = JSON.parse(e.data);
                    errorMsg = data.message || data.error || errorMsg;
                } catch { /* raw event */ }
                console.error('[UberRoute]', errorMsg);
                stream.close();
                streamRef.current = null;
                setIsRouting(false);
            });
            stream.onerror = (reason) => {
                console.error('[UberRoute]', reason || 'Connection lost');
                streamRef.current = null;
                setIsRouting(false);
            };
        } catch (err) {
            console.error('[UberRoute]', err instanceof Error ? err.message : err);
            setIsRouting(false);
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
        <Box h="100vh" w="100%" bg="var(--bg-page)" display="flex" flexDirection="column"
            alignItems="center" justifyContent="center" overflow="hidden" px={4}>

            <VStack gap={6} w="100%" maxW="48rem" mb={12}>
                {/* Hero */}
                <VStack gap={3}>
                    <Image src="/logo192.png" alt="Açaí" w="64px" h="64px" />
                    <Text fontSize="xl" fontWeight="semibold" color="var(--text-heading)">
                        What can I help you with?
                    </Text>
                    <Text fontSize="sm" color="var(--text-tertiary)" textAlign="center">
                        Your message will be routed to the right conversation automatically.
                    </Text>
                </VStack>

                {/* Input */}
                <Box w="100%">
                    <HStack mb={2} justify="flex-start" gap={3}>
                        <NativeSelect.Root size="xs" w="auto">
                            <NativeSelect.Field
                                value={selectedAgent}
                                onChange={e => { const v = e.target.value; setSelectedAgent(v); localStorage.setItem('acai.agent', v); }}
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
                                onChange={e => { const v = e.target.value; setSelectedProvider(v); localStorage.setItem('acai.provider', v); }}
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
                        {canThink && (
                            <Button
                                size="xs"
                                variant="ghost"
                                h="26px"
                                px={2}
                                fontSize="xs"
                                borderRadius="md"
                                border="1px solid"
                                borderColor={thinkingEnabled ? 'var(--accent)' : 'var(--border-input)'}
                                color={thinkingEnabled ? 'var(--accent)' : 'var(--text-tertiary)'}
                                bg={thinkingEnabled ? 'color-mix(in srgb, var(--accent) 10%, transparent)' : 'var(--bg-input)'}
                                _hover={{ borderColor: 'var(--accent)', color: 'var(--accent)' }}
                                onClick={() => {
                                    const next = !thinkingEnabled;
                                    setThinkingEnabled(next);
                                    localStorage.setItem('acai.thinking', next ? 'native' : 'off');
                                }}
                            >
                                Think{thinkingEnabled ? ': On' : ': Off'}
                            </Button>
                        )}
                    </HStack>
                    <HStack gap={2} align="flex-end">
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
                                placeholder="Ask anything..."
                                disabled={isRouting}
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
                            disabled={isRouting || !input.trim()}
                            colorScheme="green" size="lg" borderRadius="xl"
                            h="50px" w="50px" flexShrink={0}
                            type="button">
                            {isRouting ? <Spinner size="sm" /> : <SendIcon />}
                        </IconButton>
                    </HStack>
                </Box>

                {isRouting && !routePending && (
                    <HStack gap={2}>
                        <Spinner size="xs" color="var(--accent)" />
                        <Text fontSize="xs" color="var(--text-secondary)">
                            Finding the right conversation...
                        </Text>
                    </HStack>
                )}

                {routePending && (
                    <Box w="100%" bg="rgba(102,126,234,0.08)" py={3} px={4}
                        borderRadius="xl" border="1px solid" borderColor="var(--border-secondary)">
                        <HStack gap={3} justify="space-between" flexWrap="wrap">
                            <HStack gap={2} flex={1} minW={0}>
                                <Box w="8px" h="8px" borderRadius="full"
                                    bg="linear-gradient(135deg, #667eea, #764ba2)" flexShrink={0} />
                                <Text fontSize="sm" color="var(--text-secondary)" isTruncated>
                                    {routePending.is_new
                                        ? `New conversation: "${routePending.title || 'Untitled'}"`
                                        : `Continue in "${routePending.title || 'Untitled'}"`}
                                </Text>
                            </HStack>
                            <HStack gap={2} flexShrink={0}>
                                <Box as="button" onClick={acceptRoute}
                                    position="relative" overflow="hidden"
                                    px={4} py={1.5} borderRadius="md" fontSize="sm" fontWeight="semibold"
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
                                    px={4} py={1.5} borderRadius="md" fontSize="sm" fontWeight="semibold"
                                    bg="var(--bg-card)" color="var(--text-secondary)" cursor="pointer"
                                    border="1px solid" borderColor="var(--border-secondary)"
                                    _hover={{ bg: 'var(--bg-hover)' }}>
                                    New Chat
                                </Box>
                            </HStack>
                        </HStack>
                    </Box>
                )}
            </VStack>
        </Box>
    );
};

export default Home;
