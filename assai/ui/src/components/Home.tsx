import { useState, useEffect, useRef, type KeyboardEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Box, VStack, HStack, Text, Textarea, IconButton, Spinner, NativeSelect } from '@chakra-ui/react';
import { uberConverse, listProviders, listAgents } from '../services/api';
import type { AgentDef, Provider } from '../services/types';

const SendIcon = ({ size = 20 }: { size?: number }) => (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="currentColor">
        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
);

const ThinkingIcon = ({ active }: { active: boolean }) => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"
        stroke={active ? 'var(--accent)' : 'currentColor'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
        <path d="M12 2a7 7 0 0 0-4 12.7V17a1 1 0 0 0 1 1h6a1 1 0 0 0 1-1v-2.3A7 7 0 0 0 12 2z" />
        <line x1="9" y1="21" x2="15" y2="21" />
        <line x1="10" y1="24" x2="14" y2="24" />
    </svg>
);

const Home = () => {
    const navigate = useNavigate();
    const [input, setInput] = useState('');
    const [isRouting, setIsRouting] = useState(false);
    const [providers, setProviders] = useState<Provider[]>([]);
    const [agents, setAgents] = useState<AgentDef[]>([]);
    const [selectedProvider, setSelectedProvider] = useState('auto');
    const [selectedAgent, setSelectedAgent] = useState('default');
    const [enableThinking, setEnableThinking] = useState(true);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    useEffect(() => {
        document.title = 'ASSAI';
        listProviders().then(setProviders).catch(() => {});
        listAgents().then(setAgents).catch(() => {});
    }, []);

    const handleSend = async () => {
        const text = input.trim();
        if (!text || isRouting) return;

        setIsRouting(true);
        try {
            const resp = await uberConverse(text, '', selectedProvider, selectedAgent, true);
            navigate(`/conversations/${resp.conversation}`, {
                state: { pendingMessage: text, provider: selectedProvider, agent: selectedAgent, enableThinking },
            });
        } catch {
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
                    <Box w="64px" h="64px" borderRadius="xl"
                        display="flex" alignItems="center" justifyContent="center"
                        fontSize="xl" color="white" fontWeight="bold"
                        bg="var(--bg-brand-icon)">
                        SAI
                    </Box>
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
                        <IconButton
                            aria-label={enableThinking ? 'Disable reasoning' : 'Enable reasoning'}
                            onClick={() => setEnableThinking(v => !v)}
                            variant="ghost" size="xs"
                            color={enableThinking ? 'var(--accent)' : 'var(--text-muted)'}
                            _hover={{ bg: 'var(--bg-hover)' }}
                            title={enableThinking ? 'Thinking: ON' : 'Thinking: OFF'}
                            borderRadius="md"
                            h="26px" w="26px">
                            <ThinkingIcon active={enableThinking} />
                        </IconButton>
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

                {isRouting && (
                    <HStack gap={2}>
                        <Spinner size="xs" color="var(--accent)" />
                        <Text fontSize="xs" color="var(--text-secondary)">
                            Finding the right conversation...
                        </Text>
                    </HStack>
                )}
            </VStack>
        </Box>
    );
};

export default Home;
