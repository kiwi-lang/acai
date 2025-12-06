import { Box, HStack, VStack, Text, Button } from '@chakra-ui/react';
import { useEffect, useRef } from 'react';
import { Message } from '../services/types';

interface ChatMessageProps {
    message: Message;
    onRetry?: (prompt: string, messageId?: string) => void;
}

const UserIcon = () => (
    <Box
        w="32px"
        h="32px"
        bg="purple.500"
        borderRadius="sm"
        display="flex"
        alignItems="center"
        justifyContent="center"
        color="white"
        fontWeight="bold"
        fontSize="sm"
    >
        U
    </Box>
);

const AssistantIcon = () => (
    <Box
        w="32px"
        h="32px"
        bg="green.500"
        borderRadius="sm"
        display="flex"
        alignItems="center"
        justifyContent="center"
        color="white"
        fontWeight="bold"
        fontSize="sm"
    >
        AI
    </Box>
);

const ChatMessage = ({ message, onRetry }: ChatMessageProps) => {
    const isUser = message.role === 'user';
    const isError = message.retryPrompt !== undefined;
    const logsEndRef = useRef<HTMLDivElement>(null);

    // Auto-scroll logs to bottom when new logs arrive
    useEffect(() => {
        if (message.logs && message.logs.length > 0 && logsEndRef.current) {
            logsEndRef.current.scrollIntoView({ behavior: 'smooth' });
        }
    }, [message.logs]);

    return (
        <Box
            w="100%"
            bg={isUser ? 'transparent' : 'gray.50'}
            _dark={{ bg: isUser ? 'transparent' : 'gray.800' }}
            py={6}
            px={4}
        >
            <HStack
                maxW="48rem"
                mx="auto"
                align="flex-start"
                gap={4}
            >
                {/* Avatar */}
                {isUser ? <UserIcon /> : <AssistantIcon />}

                {/* Message Content */}
                <VStack align="flex-start" flex={1} gap={2}>
                    <Text
                        fontWeight="semibold"
                        fontSize="sm"
                        color={isUser ? 'purple.600' : 'green.600'}
                        _dark={{ color: isUser ? 'purple.300' : 'green.300' }}
                    >
                        {isUser ? 'You' : 'Assistant'}
                    </Text>

                    {/* Text Content */}
                    {message.content && (
                        <Box w="100%">
                            <Text
                                fontSize="md"
                                lineHeight="1.75"
                                whiteSpace="pre-wrap"
                                wordBreak="break-word"
                                color={isError ? 'red.600' : undefined}
                                _dark={{ color: isError ? 'red.400' : undefined }}
                            >
                                {message.content}
                            </Text>
                            {isError && onRetry && message.retryPrompt && (
                                <Button
                                    size="sm"
                                    colorScheme="purple"
                                    variant="outline"
                                    mt={3}
                                    onClick={() => onRetry(message.retryPrompt!, message.id)}
                                >
                                    🔄 Retry
                                </Button>
                            )}
                        </Box>
                    )}

                    {/* Logs Display - Show when generating and no image yet */}
                    {message.isGenerating && message.logs && message.logs.length > 0 && !message.imageUrl && (
                        <Box
                            w="100%"
                            mt={2}
                            p={3}
                            bg="gray.900"
                            _dark={{ bg: 'gray.950' }}
                            borderRadius="md"
                            border="1px solid"
                            borderColor="gray.700"
                            maxH="400px"
                            overflowY="auto"
                            fontFamily="mono"
                            fontSize="xs"
                            css={{
                                '&::-webkit-scrollbar': {
                                    width: '8px',
                                },
                                '&::-webkit-scrollbar-track': {
                                    background: 'transparent',
                                },
                                '&::-webkit-scrollbar-thumb': {
                                    background: '#4a5568',
                                    borderRadius: '4px',
                                },
                                '&::-webkit-scrollbar-thumb:hover': {
                                    background: '#718096',
                                },
                            }}
                        >
                            <VStack align="stretch" gap={0}>
                                {message.logs.map((log, index) => (
                                    <Text
                                        key={index}
                                        color={log.type === 'stderr' ? 'red.400' : 'gray.300'}
                                        whiteSpace="pre-wrap"
                                        wordBreak="break-word"
                                        lineHeight="1.5"
                                    >
                                        {log.line}
                                    </Text>
                                ))}
                                <div ref={logsEndRef} />
                            </VStack>
                        </Box>
                    )}

                    {/* Image Content */}
                    {message.imageUrl && (
                        <Box
                            borderRadius="md"
                            overflow="hidden"
                            maxW="100%"
                            mt={2}
                            border="1px solid"
                            borderColor="gray.200"
                            _dark={{ borderColor: 'gray.700' }}
                            bg="gray.50"
                            p={2}
                        >
                            <Box
                                borderRadius="md"
                                overflow="hidden"
                                display="flex"
                                justifyContent="center"
                                alignItems="center"
                                bg="white"
                                _dark={{ bg: 'gray.900' }}
                            >
                                <img
                                    src={message.imageUrl}
                                    alt="Generated content"
                                    style={{
                                        maxWidth: '100%',
                                        height: 'auto',
                                        display: 'block',
                                        maxHeight: '600px',
                                        objectFit: 'contain'
                                    }}
                                />
                            </Box>
                            <HStack justify="flex-end" mt={2}>
                                <Button
                                    size="xs"
                                    variant="outline"
                                    onClick={() => {
                                        const link = document.createElement('a');
                                        link.href = message.imageUrl!;
                                        link.download = `generated-image-${Date.now()}.png`;
                                        link.click();
                                    }}
                                >
                                    Download
                                </Button>
                            </HStack>
                        </Box>
                    )}

                    {/* Audio Content */}
                    {message.audioUrl && (
                        <Box mt={2} w="100%">
                            <audio
                                controls
                                src={message.audioUrl}
                                style={{ width: '100%', maxWidth: '400px' }}
                            >
                                Your browser does not support the audio element.
                            </audio>
                        </Box>
                    )}

                    <Text fontSize="xs" color="gray.500" mt={1}>
                        {new Date(message.timestamp).toLocaleTimeString()}
                    </Text>
                </VStack>
            </HStack>
        </Box>
    );
};

export default ChatMessage;

