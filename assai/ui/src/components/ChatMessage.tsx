import { Box, HStack, VStack, Text } from '@chakra-ui/react';
import { Message } from '../services/types';

interface ChatMessageProps {
    message: Message;
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

const ChatMessage = ({ message }: ChatMessageProps) => {
    const isUser = message.role === 'user';

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
                        <Text
                            fontSize="md"
                            lineHeight="1.75"
                            whiteSpace="pre-wrap"
                            wordBreak="break-word"
                        >
                            {message.content}
                        </Text>
                    )}

                    {/* Image Content */}
                    {message.imageUrl && (
                        <Box
                            borderRadius="md"
                            overflow="hidden"
                            maxW="100%"
                            mt={2}
                        >
                            <img
                                src={message.imageUrl}
                                alt="Generated content"
                                style={{ maxWidth: '100%', height: 'auto', display: 'block' }}
                            />
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

