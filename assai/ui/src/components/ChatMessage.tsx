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
                    {(() => {
                        // Handle both legacy string format and unified Input format
                        let textContent: string | undefined;
                        let imageUrl: string | undefined;
                        let imageUrls: string[] | undefined;
                        let audioUrl: string | undefined;

                        if (typeof message.content === 'string') {
                            // Legacy format - content is string
                            textContent = message.content;
                            imageUrl = message.imageUrl;
                            imageUrls = message.imageUrls;
                            audioUrl = message.audioUrl;
                        } else if (message.content && typeof message.content === 'object') {
                            // Unified Input format
                            const input = message.content;
                            if (input.kind === 'text') {
                                textContent = input.data;
                            } else if (input.kind === 'image') {
                                imageUrl = input.data;
                                imageUrls = [input.data];
                            } else if (input.kind === 'audio') {
                                audioUrl = input.data;
                            }
                        }

                        // Use display extensions if available (for backward compatibility)
                        if (message.imageUrl) imageUrl = message.imageUrl;
                        if (message.imageUrls) imageUrls = message.imageUrls;
                        if (message.audioUrl) audioUrl = message.audioUrl;

                        return (
                            <>
                                {textContent && (
                                    <Box w="100%">
                                        <Text
                                            fontSize="md"
                                            lineHeight="1.75"
                                            whiteSpace="pre-wrap"
                                            wordBreak="break-word"
                                            color={isError ? 'red.400' : 'gray.200'}
                                        >
                                            {textContent}
                                        </Text>
                                        {isError && onRetry && message.retryPrompt && (
                                            <Button
                                                size="sm"
                                                colorScheme="purple"
                                                variant="outline"
                                                mt={3}
                                                onClick={() => onRetry(message.retryPrompt!, String(message.id))}
                                            >
                                                🔄 Retry
                                            </Button>
                                        )}
                                    </Box>
                                )}
                                {/* Image Content */}
                                {(imageUrls && imageUrls.length > 0) || imageUrl ? (
                                    <Box
                                        display="flex"
                                        flexWrap="wrap"
                                        gap={4}
                                        mt={2}
                                        w="100%"
                                    >
                                        {(imageUrls || (imageUrl ? [imageUrl] : [])).map((url, index) => (
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
                                                        src={url}
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
                                                            link.href = url;
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
                                {audioUrl && (
                                    <Box mt={2} w="100%">
                                        <audio
                                            controls
                                            src={audioUrl}
                                            style={{ width: '100%', maxWidth: '400px' }}
                                        >
                                            Your browser does not support the audio element.
                                        </audio>
                                    </Box>
                                )}
                            </>
                        );
                    })()}

                    {/* Logs Display - Show when generating (logs are hidden once response arrives) */}
                    {message.isGenerating && message.logs && message.logs.length > 0 && (
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


                    <Text fontSize="xs" color="gray.500" mt={1}>
                        {new Date(message.timestamp).toLocaleTimeString()}
                    </Text>
                </VStack>
            </HStack>
        </Box>
    );
};

export default ChatMessage;

