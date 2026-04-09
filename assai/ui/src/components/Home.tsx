import { useState, useEffect, useRef, useCallback, KeyboardEvent, useLayoutEffect } from 'react';
import { Box, VStack, HStack, Text, Textarea, IconButton, Spinner } from '@chakra-ui/react';
import { converse, getHistory, clearHistory } from '../services/api';
import { useAgentSocket } from '../contexts/WebSocketContext';
import type { AgentMessage, StreamChunk } from '../services/types';

const SendIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
);

const UserIcon = () => (
    <Box
        w="32px" h="32px" bg="purple.500" borderRadius="sm"
        display="flex" alignItems="center" justifyContent="center"
        color="white" fontWeight="bold" fontSize="sm"
    >
        U
    </Box>
);

const AssistantIcon = () => (
    <Box
        w="32px" h="32px" bg="green.500" borderRadius="sm"
        display="flex" alignItems="center" justifyContent="center"
        color="white" fontWeight="bold" fontSize="sm"
    >
        AI
    </Box>
);

const Home = () => {
    const [messages, setMessages] = useState<AgentMessage[]>([]);
    const [input, setInput] = useState('');
    const [isLoading, setIsLoading] = useState(false);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);
    const shouldRestoreFocusRef = useRef(false);
    const activeTaskRef = useRef<string | null>(null);

    const { onChunk, onStreamEnd } = useAgentSocket();

    useEffect(() => {
        document.title = 'Conversation - ASSAI';
        getHistory()
            .then(setMessages)
            .catch(() => {});
    }, []);

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

    useEffect(() => {
        const unsub1 = onChunk(handleChunk);
        const unsub2 = onStreamEnd(handleStreamEnd);
        return () => { unsub1(); unsub2(); };
    }, [onChunk, onStreamEnd, handleChunk, handleStreamEnd]);

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
            const taskId = await converse(text);
            activeTaskRef.current = taskId;
            setMessages(prev => [
                ...prev,
                { role: 'assistant', content: '', isStreaming: true, taskId },
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
        <Box display="flex" flexDirection="column" h="100vh" w="100%" bg="gray.900" overflow="hidden">
            {/* Messages */}
            <Box flex={1} overflowY="auto" w="100%" minH={0}>
                {messages.length === 0 ? (
                    <VStack flex={1} justify="center" align="center" p={8} gap={6} minH="60vh">
                        <Box
                            w="64px" h="64px" bg="green.500" borderRadius="xl"
                            display="flex" alignItems="center" justifyContent="center"
                            fontSize="2xl" color="white" fontWeight="bold"
                        >
                            AI
                        </Box>
                        <VStack gap={2} textAlign="center">
                            <Text fontSize="2xl" fontWeight="semibold" color="white">
                                ASSAI Agent
                            </Text>
                            <Text fontSize="md" color="gray.400" maxW="md">
                                Describe your project goals, discuss features, or request work.
                                The agent will help plan and schedule tasks.
                            </Text>
                        </VStack>
                        <VStack gap={2} w="100%" maxW="2xl" mt={4}>
                            <Text fontSize="sm" fontWeight="semibold" color="gray.300">Try:</Text>
                            {[
                                'Describe the project architecture',
                                'Add a new REST endpoint for user profiles',
                                'What does the current spec say about testing?',
                            ].map(example => (
                                <Box
                                    key={example} p={3} bg="gray.800" borderRadius="lg" w="100%"
                                    fontSize="sm" color="gray.300" cursor="pointer"
                                    _hover={{ bg: 'gray.700' }}
                                    onClick={() => { setInput(example); textareaRef.current?.focus(); }}
                                >
                                    {example}
                                </Box>
                            ))}
                        </VStack>
                    </VStack>
                ) : (
                    <VStack gap={0} w="100%">
                        {messages.map((msg, i) => (
                            <Box key={i} w="100%" bg={msg.role === 'user' ? 'transparent' : 'gray.800'} py={6} px={4}>
                                <HStack maxW="48rem" mx="auto" align="flex-start" gap={4}>
                                    {msg.role === 'user' ? <UserIcon /> : <AssistantIcon />}
                                    <VStack align="flex-start" flex={1} gap={1}>
                                        <Text fontWeight="semibold" fontSize="sm"
                                            color={msg.role === 'user' ? 'purple.300' : 'green.300'}>
                                            {msg.role === 'user' ? 'You' : 'Agent'}
                                        </Text>
                                        <Text fontSize="md" lineHeight="1.75" whiteSpace="pre-wrap"
                                            wordBreak="break-word" color="gray.200">
                                            {msg.content}
                                            {msg.isStreaming && (
                                                <Box as="span" display="inline-block" w="2px" h="1em"
                                                    bg="green.400" ml={0.5}
                                                    animation="blink 1s step-start infinite" />
                                            )}
                                        </Text>
                                    </VStack>
                                </HStack>
                            </Box>
                        ))}
                        {isLoading && !messages.some(m => m.isStreaming) && (
                            <Box w="100%" bg="gray.800" py={6} px={4}>
                                <HStack maxW="48rem" mx="auto" align="flex-start" gap={4}>
                                    <AssistantIcon />
                                    <HStack gap={2}>
                                        <Spinner size="sm" color="green.300" />
                                        <Text fontSize="sm" color="gray.400">Thinking...</Text>
                                    </HStack>
                                </HStack>
                            </Box>
                        )}
                        <div ref={messagesEndRef} />
                    </VStack>
                )}
            </Box>

            {/* Input */}
            <Box w="100%" bg="gray.900" borderTop="1px solid" borderColor="gray.700" py={4} px={4}>
                <HStack maxW="48rem" mx="auto" gap={2} align="flex-end">
                    <HStack
                        flex={1} bg="gray.800" borderRadius="xl" border="1px solid" borderColor="gray.600"
                        _focusWithin={{ borderColor: 'green.500', boxShadow: '0 0 0 1px var(--chakra-colors-green-500)' }}
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
                            color="gray.100" _placeholder={{ color: 'gray.500' }}
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
    );
};

export default Home;
