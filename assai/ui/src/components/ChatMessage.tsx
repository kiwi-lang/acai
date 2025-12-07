import { Box, HStack, VStack, Text, Button, Spinner } from '@chakra-ui/react';
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
            bg={isUser ? 'transparent' : 'gray.800'}
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
                    <HStack gap={2} align="center">
                        <Text
                            fontWeight="semibold"
                            fontSize="sm"
                            color={isUser ? 'purple.300' : 'green.300'}
                        >
                            {isUser ? 'You' : 'Assistant'}
                        </Text>
                        {!isUser && message.isGenerating && (
                            <Spinner size="xs" color="green.300" />
                        )}
                    </HStack>

                    {/* Text Content */}
                    {message.content && (
                        <Box w="100%">
                            <Text
                                fontSize="md"
                                lineHeight="1.75"
                                whiteSpace="pre-wrap"
                                wordBreak="break-word"
                                color={isError ? 'red.400' : 'gray.200'}
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
                    {(message.imageUrls && message.imageUrls.length > 0) || message.imageUrl ? (
                        <Box
                            display="flex"
                            flexWrap="wrap"
                            gap={4}
                            mt={2}
                            w="100%"
                        >
                            {(message.imageUrls || (message.imageUrl ? [message.imageUrl] : [])).map((imageUrl, index) => (
                                <Box
                                    key={index}
                                    borderRadius="md"
                                    overflow="hidden"
                                    flex="1 1 auto"
                                    minW="200px"
                                    maxW="100%"
                                    border="1px solid"
                                    borderColor="gray.700"
                                    bg="gray.800"
                                    p={2}
                                >
                                    <Box
                                        borderRadius="md"
                                        overflow="hidden"
                                        display="flex"
                                        justifyContent="center"
                                        alignItems="center"
                                        bg="gray.900"
                                    >
                                        <img
                                            src={imageUrl}
                                            alt={`Generated image ${index + 1}`}
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
                                                link.href = imageUrl;
                                                link.download = `generated-image-${Date.now()}-${index + 1}.png`;
                                                link.click();
                                            }}
                                            padding="5px"
                                            color="gray.200"
                                            borderColor="gray.600"
                                            _hover={{ bg: 'gray.700', borderColor: 'gray.500' }}
                                        >
                                            Download
                                        </Button>
                                    </HStack>
                                </Box>
                            ))}
                        </Box>
                    ) : null}

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

