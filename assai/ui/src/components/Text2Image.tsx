import { useState, useEffect, useRef } from 'react';
import {
    Box,
    VStack,
    HStack,
    Spinner,
    Text,
    Button,
    Input,
} from '@chakra-ui/react';
import ChatMessage from './ChatMessage';
import ChatInput from './ChatInput';
import { Message } from '../services/types';
import { assaiAPI, ImageGenerationParams } from '../services/api';
import { websocketService, WebSocketMessage } from '../services/websocket';

const SettingsIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="3" />
        <path d="M12 1v6m0 6v6M5.64 5.64l4.24 4.24m4.24 4.24l4.24 4.24M1 12h6m6 0h6M5.64 18.36l4.24-4.24m4.24-4.24l4.24-4.24" />
    </svg>
);

const ChevronDownIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="6 9 12 15 18 9" />
    </svg>
);

const ChevronUpIcon = () => (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <polyline points="18 15 12 9 6 15" />
    </svg>
);

const Text2Image = () => {
    const [messages, setMessages] = useState<Message[]>([]);
    const [isLoading, setIsLoading] = useState(false);
    const [showSettings, setShowSettings] = useState(false);
    const [statusMessage, setStatusMessage] = useState<string>('');
    const [generationProgress, setGenerationProgress] = useState<number>(0);
    const messagesEndRef = useRef<HTMLDivElement>(null);
    const currentPromptRef = useRef<string>('');

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<ImageGenerationParams>({
        height: 256,
        width: 256,
        guidance_scale: 3.5,
        num_inference_steps: 50,
        max_sequence_length: 512,
        seed: 0,
    });

    useEffect(() => {
        document.title = 'Text to Image - ASSAI';

        // Connect to WebSocket
        websocketService.connect();

        // Set up WebSocket listeners
        const unsubscribeModelLoading = websocketService.on('model_loading', (msg: WebSocketMessage) => {
            setStatusMessage(msg.data.message || 'Loading model...');
        });

        const unsubscribeModelLoaded = websocketService.on('model_loaded', (msg: WebSocketMessage) => {
            setStatusMessage(msg.data.message || 'Model loaded');
        });

        const unsubscribeGenerationStarted = websocketService.on('generation_started', (msg: WebSocketMessage) => {
            setStatusMessage(msg.data.message || 'Starting generation...');
            setGenerationProgress(0);
        });

        const unsubscribeGenerationProgress = websocketService.on('generation_progress', (msg: WebSocketMessage) => {
            if (msg.data.progress !== undefined) {
                setGenerationProgress(msg.data.progress * 100);
            }
            if (msg.data.message) {
                setStatusMessage(msg.data.message);
            }
        });

        const unsubscribeGenerationComplete = websocketService.on('generation_complete', (msg: WebSocketMessage) => {
            setStatusMessage('');
            setGenerationProgress(0);
            setIsLoading(false);

            if (msg.data.images && msg.data.images.length > 0) {
                const imageUrl = msg.data.images[0];
                const assistantMessage: Message = {
                    id: (Date.now() + 1).toString(),
                    role: 'assistant',
                    content: `Generated image for: "${currentPromptRef.current}"`,
                    timestamp: new Date(),
                    type: 'image',
                    imageUrl,
                };
                setMessages(prev => [...prev, assistantMessage]);
            }
        });

        const unsubscribeError = websocketService.on('error', (msg: WebSocketMessage) => {
            setStatusMessage('');
            setGenerationProgress(0);
            setIsLoading(false);

            const errorMessage = msg.data.message || msg.data.error || 'An error occurred';
            const errorMsg: Message = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: `Sorry, I encountered an error: ${errorMessage}. Please try again.`,
                timestamp: new Date(),
                type: 'text',
                retryPrompt: currentPromptRef.current,
            };
            setMessages(prev => [...prev, errorMsg]);
        });

        // Cleanup on unmount
        return () => {
            unsubscribeModelLoading();
            unsubscribeModelLoaded();
            unsubscribeGenerationStarted();
            unsubscribeGenerationProgress();
            unsubscribeGenerationComplete();
            unsubscribeError();
        };
    }, []);

    useEffect(() => {
        // Scroll to bottom when new messages arrive
        messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
    }, [messages]);

    const handleSendMessage = async (content: string, _imageFile?: File, _audioFile?: File) => {
        if (!content.trim()) {
            return;
        }

        currentPromptRef.current = content.trim();

        // Add user message
        const userMessage: Message = {
            id: Date.now().toString(),
            role: 'user',
            content: content.trim(),
            timestamp: new Date(),
            type: 'text',
        };

        setMessages(prev => [...prev, userMessage]);
        setIsLoading(true);
        setStatusMessage('Initializing...');
        setGenerationProgress(0);

        try {
            // Get session ID from WebSocket service
            const sessionId = websocketService.getSessionId();

            // Generate image from prompt with current generation parameters
            // Backend will send WebSocket messages for progress updates
            // The final result will be handled by the WebSocket 'generation_complete' event
            await assaiAPI.generateImage(content.trim(), generationParams, undefined, sessionId || undefined);

            // Note: The actual image will be added via WebSocket 'generation_complete' event
            // If WebSocket fails, we fall back to the HTTP response
        } catch (error) {
            console.error('Failed to generate image:', error);
            const errorMessage = error instanceof Error ? error.message : 'Failed to generate image';

            setStatusMessage('');
            setGenerationProgress(0);
            setIsLoading(false);

            // Add error message with retry prompt stored
            const errorMsg: Message = {
                id: (Date.now() + 1).toString(),
                role: 'assistant',
                content: `Sorry, I encountered an error: ${errorMessage}. Please try again.`,
                timestamp: new Date(),
                type: 'text',
                retryPrompt: content.trim(), // Store the prompt for retry
            };

            setMessages(prev => [...prev, errorMsg]);
        }
    };

    const handleRetry = async (prompt: string, errorMessageId?: string) => {
        // Remove the error message if we know its ID
        if (errorMessageId) {
            setMessages(prev => prev.filter(msg => msg.id !== errorMessageId));
        }
        // Retry the prompt with current generation parameters
        await handleSendMessage(prompt);
    };

    const resetToDefaults = () => {
        setGenerationParams({
            height: 256,
            width: 256,
            guidance_scale: 3.5,
            num_inference_steps: 50,
            max_sequence_length: 512,
            seed: 0,
        });
    };

    const EmptyState = () => (
        <VStack
            flex={1}
            justify="center"
            align="center"
            p={8}
            gap={6}
        >
            <Box
                w="64px"
                h="64px"
                bg="purple.500"
                borderRadius="xl"
                display="flex"
                alignItems="center"
                justifyContent="center"
                fontSize="2xl"
                color="white"
                fontWeight="bold"
            >
                🎨
            </Box>

            <VStack gap={2} textAlign="center">
                <Text fontSize="2xl" fontWeight="semibold">
                    Text to Image
                </Text>
                <Text fontSize="md" color="gray.600" maxW="md">
                    Describe the image you want to generate, and I'll create it for you.
                    Each prompt will generate a new image.
                </Text>
            </VStack>

            <VStack gap={3} w="100%" maxW="2xl" mt={4}>
                <Text fontSize="sm" fontWeight="semibold" color="gray.700">
                    Try prompts like:
                </Text>
                <VStack gap={2} w="100%">
                    {[
                        'A serene sunset over mountains',
                        'A futuristic cityscape at night',
                        'A cute cat playing with yarn',
                        'An abstract painting with vibrant colors',
                    ].map((example) => (
                        <Box
                            key={example}
                            p={3}
                            bg="gray.50"
                            _dark={{ bg: 'gray.800' }}
                            borderRadius="lg"
                            w="100%"
                            fontSize="sm"
                            color="gray.700"
                            cursor="pointer"
                            _hover={{ bg: 'gray.100', _dark: { bg: 'gray.700' } }}
                            onClick={() => handleSendMessage(example)}
                        >
                            {example}
                        </Box>
                    ))}
                </VStack>
            </VStack>
        </VStack>
    );

    return (
        <Box
            display="flex"
            flexDirection="column"
            h="100vh"
            w="100%"
            bg="white"
            _dark={{ bg: 'gray.900' }}
        >
            {/* Messages Area */}
            <Box
                flex={1}
                overflowY="auto"
                w="100%"
            >
                {messages.length === 0 ? (
                    <EmptyState />
                ) : (
                    <VStack gap={0} w="100%">
                        {messages.map((message) => (
                            <ChatMessage
                                key={message.id}
                                message={message}
                                onRetry={handleRetry}
                            />
                        ))}

                        {isLoading && (
                            <Box
                                w="100%"
                                bg="gray.50"
                                _dark={{ bg: 'gray.800' }}
                                py={6}
                                px={4}
                            >
                                <Box maxW="48rem" mx="auto">
                                    <VStack align="flex-start" gap={3}>
                                        <HStack gap={4}>
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
                                                🎨
                                            </Box>
                                            <VStack align="flex-start" gap={1} flex={1}>
                                                <Text fontSize="sm" color="gray.600" fontWeight="medium">
                                                    {statusMessage || 'Generating your image...'}
                                                </Text>
                                                {generationProgress > 0 && (
                                                    <Box w="100%" pt={1}>
                                                        <Box
                                                            w="100%"
                                                            h="8px"
                                                            bg="gray.200"
                                                            _dark={{ bg: 'gray.700' }}
                                                            borderRadius="full"
                                                            overflow="hidden"
                                                        >
                                                            <Box
                                                                h="100%"
                                                                bg="purple.500"
                                                                w={`${generationProgress}%`}
                                                                transition="width 0.3s ease"
                                                            />
                                                        </Box>
                                                        <Text fontSize="xs" color="gray.500" mt={1}>
                                                            {Math.round(generationProgress)}%
                                                        </Text>
                                                    </Box>
                                                )}
                                                {generationProgress === 0 && (
                                                    <Spinner size="sm" color="purple.500" />
                                                )}
                                            </VStack>
                                        </HStack>
                                    </VStack>
                                </Box>
                            </Box>
                        )}

                        <div ref={messagesEndRef} />
                    </VStack>
                )}
            </Box>

            {/* Settings Panel */}
            <Box
                borderTop="1px solid"
                borderColor="gray.200"
                bg="gray.50"
                _dark={{ borderColor: 'gray.700', bg: 'gray.800' }}
            >
                <HStack
                    px={4}
                    py={2}
                    justify="space-between"
                    cursor="pointer"
                    onClick={() => setShowSettings(!showSettings)}
                    _hover={{ bg: 'gray.100', _dark: { bg: 'gray.700' } }}
                >
                    <HStack gap={2}>
                        <SettingsIcon />
                        <Text fontSize="sm" fontWeight="medium">
                            Generation Settings
                        </Text>
                    </HStack>
                    {showSettings ? <ChevronUpIcon /> : <ChevronDownIcon />}
                </HStack>

                {showSettings && (
                    <Box px={4} py={4}>
                        <VStack gap={4} align="stretch">
                            {/* Width and Height */}
                            <HStack gap={4}>
                                <VStack align="flex-start" gap={1} flex={1}>
                                    <Text fontSize="sm" fontWeight="medium">Width</Text>
                                    <Input
                                        type="number"
                                        value={generationParams.width}
                                        onChange={(e) => {
                                            const value = parseInt(e.target.value) || 256;
                                            setGenerationParams(prev => ({ ...prev, width: value }));
                                        }}
                                        min={64}
                                        max={2048}
                                        step={64}
                                        size="sm"
                                    />
                                </VStack>

                                <VStack align="flex-start" gap={1} flex={1}>
                                    <Text fontSize="sm" fontWeight="medium">Height</Text>
                                    <Input
                                        type="number"
                                        value={generationParams.height}
                                        onChange={(e) => {
                                            const value = parseInt(e.target.value) || 256;
                                            setGenerationParams(prev => ({ ...prev, height: value }));
                                        }}
                                        min={64}
                                        max={2048}
                                        step={64}
                                        size="sm"
                                    />
                                </VStack>
                            </HStack>

                            {/* Guidance Scale */}
                            <VStack align="flex-start" gap={1}>
                                <Text fontSize="sm" fontWeight="medium">
                                    Guidance Scale: {generationParams.guidance_scale?.toFixed(1)}
                                </Text>
                                <Input
                                    type="number"
                                    value={generationParams.guidance_scale}
                                    onChange={(e) => {
                                        const value = parseFloat(e.target.value) || 3.5;
                                        setGenerationParams(prev => ({ ...prev, guidance_scale: value }));
                                    }}
                                    min={1}
                                    max={20}
                                    step={0.1}
                                    size="sm"
                                />
                            </VStack>

                            {/* Inference Steps */}
                            <VStack align="flex-start" gap={1}>
                                <Text fontSize="sm" fontWeight="medium">Inference Steps</Text>
                                <Input
                                    type="number"
                                    value={generationParams.num_inference_steps}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value) || 50;
                                        setGenerationParams(prev => ({ ...prev, num_inference_steps: value }));
                                    }}
                                    min={1}
                                    max={100}
                                    size="sm"
                                />
                            </VStack>

                            {/* Max Sequence Length */}
                            <VStack align="flex-start" gap={1}>
                                <Text fontSize="sm" fontWeight="medium">Max Sequence Length</Text>
                                <Input
                                    type="number"
                                    value={generationParams.max_sequence_length}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value) || 512;
                                        setGenerationParams(prev => ({ ...prev, max_sequence_length: value }));
                                    }}
                                    min={128}
                                    max={2048}
                                    step={128}
                                    size="sm"
                                />
                            </VStack>

                            {/* Seed */}
                            <VStack align="flex-start" gap={1}>
                                <Text fontSize="sm" fontWeight="medium">Seed (0 = random)</Text>
                                <Input
                                    type="number"
                                    value={generationParams.seed}
                                    onChange={(e) => {
                                        const value = parseInt(e.target.value) || 0;
                                        setGenerationParams(prev => ({ ...prev, seed: value }));
                                    }}
                                    min={0}
                                    max={2147483647}
                                    size="sm"
                                />
                            </VStack>

                            <Button
                                size="sm"
                                variant="outline"
                                onClick={resetToDefaults}
                                w="fit-content"
                            >
                                Reset to Defaults
                            </Button>
                        </VStack>
                    </Box>
                )}
            </Box>

            {/* Input Area */}
            <ChatInput
                onSendMessage={handleSendMessage}
                disabled={isLoading}
                placeholder={messages.length === 0 ? "Describe the image you want to generate..." : "Describe another image..."}
            />
        </Box>
    );
};

export default Text2Image;

