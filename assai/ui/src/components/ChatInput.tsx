import { useState, useRef, KeyboardEvent } from 'react';
import { Box, Textarea, IconButton, HStack, Text, VStack, Image } from '@chakra-ui/react';
import FileUpload from './FileUpload';

interface ChatInputProps {
    onSendMessage: (message: string, imageFile?: File, audioFile?: File) => void;
    disabled?: boolean;
    placeholder?: string;
}

const SendIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="currentColor">
        <path d="M2.01 21L23 12 2.01 3 2 10l15 2-15 2z" />
    </svg>
);

const CloseIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor">
        <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z" />
    </svg>
);

const ChatInput = ({ onSendMessage, disabled = false, placeholder = "Send a message..." }: ChatInputProps) => {
    const [message, setMessage] = useState('');
    const [imageFile, setImageFile] = useState<File | null>(null);
    const [audioFile, setAudioFile] = useState<File | null>(null);
    const [imagePreview, setImagePreview] = useState<string | null>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const handleSend = () => {
        if ((message.trim() || imageFile || audioFile) && !disabled) {
            onSendMessage(
                message.trim(),
                imageFile || undefined,
                audioFile || undefined
            );
            setMessage('');
            setImageFile(null);
            setAudioFile(null);
            setImagePreview(null);
            // Reset textarea height
            if (textareaRef.current) {
                textareaRef.current.style.height = 'auto';
            }
        }
    };

    const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
        if (e.key === 'Enter' && !e.shiftKey) {
            e.preventDefault();
            handleSend();
        }
    };

    const handleChange = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
        setMessage(e.target.value);
        // Auto-resize textarea
        e.target.style.height = 'auto';
        e.target.style.height = Math.min(e.target.scrollHeight, 200) + 'px';
    };

    const handleImageUpload = (file: File) => {
        setImageFile(file);
        // Create preview
        const reader = new FileReader();
        reader.onload = (e) => {
            setImagePreview(e.target?.result as string);
        };
        reader.readAsDataURL(file);
    };

    const handleAudioUpload = (file: File) => {
        setAudioFile(file);
    };

    const removeImage = () => {
        setImageFile(null);
        setImagePreview(null);
    };

    const removeAudio = () => {
        setAudioFile(null);
    };

    return (
        <Box
            position="sticky"
            bottom={0}
            w="100%"
            bg="gray.900"
            borderTop="1px solid"
            borderColor="gray.700"
            py={4}
            px={4}
        >
            <Box maxW="48rem" mx="auto">
                <VStack gap={3} align="stretch">
                    {/* File Previews */}
                    {(imagePreview || audioFile) && (
                        <HStack gap={3} flexWrap="wrap">
                            {imagePreview && (
                                <Box position="relative">
                                    <Image
                                        src={imagePreview}
                                        alt="Upload preview"
                                        maxH="100px"
                                        borderRadius="md"
                                        border="1px solid"
                                        borderColor="gray.600"
                                    />
                                    <IconButton
                                        aria-label="Remove image"
                                        size="xs"
                                        position="absolute"
                                        top={1}
                                        right={1}
                                        onClick={removeImage}
                                        colorScheme="red"
                                        borderRadius="full"
                                    >
                                        <CloseIcon />
                                    </IconButton>
                                </Box>
                            )}

                            {audioFile && (
                                <Box
                                    p={3}
                                    bg="gray.700"
                                    borderRadius="md"
                                    position="relative"
                                >
                                    <HStack gap={2}>
                                        <Text fontSize="sm" fontWeight="medium">
                                            🎵 {audioFile.name}
                                        </Text>
                                        <IconButton
                                            aria-label="Remove audio"
                                            size="xs"
                                            onClick={removeAudio}
                                            variant="ghost"
                                            colorScheme="red"
                                        >
                                            <CloseIcon />
                                        </IconButton>
                                    </HStack>
                                </Box>
                            )}
                        </HStack>
                    )}

                    {/* Input Area */}
                    <HStack gap={2} align="flex-end">
                        <HStack
                            flex={1}
                            bg="gray.800"
                            borderRadius="xl"
                            border="1px solid"
                            borderColor="gray.600"
                            _focusWithin={{
                                borderColor: 'green.500',
                                boxShadow: '0 0 0 1px var(--chakra-colors-green-500)'
                            }}
                            align="flex-end"
                            px={2}
                        >
                            <FileUpload
                                onImageUpload={handleImageUpload}
                                onAudioUpload={handleAudioUpload}
                                disabled={disabled}
                            />

                            <Textarea
                                ref={textareaRef}
                                value={message}
                                onChange={handleChange}
                                onKeyDown={handleKeyDown}
                                placeholder={placeholder}
                                disabled={disabled}
                                rows={1}
                                resize="none"
                                border="none"
                                _focus={{ outline: 'none', boxShadow: 'none' }}
                                py={3}
                                px={2}
                                fontSize="md"
                                maxH="200px"
                                overflow="auto"
                                bg="transparent"
                                flex={1}
                                color="gray.100"
                                _placeholder={{ color: 'gray.500' }}
                            />
                        </HStack>

                        <IconButton
                            aria-label="Send message"
                            onClick={handleSend}
                            disabled={disabled || (!message.trim() && !imageFile && !audioFile)}
                            colorScheme="green"
                            size="lg"
                            borderRadius="xl"
                            h="50px"
                            w="50px"
                            flexShrink={0}
                        >
                            <SendIcon />
                        </IconButton>
                    </HStack>
                </VStack>

            </Box>
        </Box>
    );
};

export default ChatInput;

