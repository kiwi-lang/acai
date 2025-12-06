import { Box, HStack, VStack, Text, Button } from '@chakra-ui/react';
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

