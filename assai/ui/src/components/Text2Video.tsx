import { useState } from 'react';
import { VStack, Input, Button, HStack, Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import { Message } from '../services/types';
import { assaiAPI, VideoGenerationParams } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Text2Video = () => {
    const { sessionId } = useWebSocket();

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<VideoGenerationParams>({
        height: 512,
        width: 512,
        num_frames: 49,
        num_inference_steps: 50,
        seed: 0,
    });

    const resetToDefaults = () => {
        setGenerationParams({
            height: 512,
            width: 512,
            num_frames: 49,
            num_inference_steps: 50,
            seed: 0,
        });
    };

    const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
        return await assaiAPI.generateVideo(
            typeof userMessage.content === 'object' && userMessage.content.kind === 'text'
                ? userMessage.content.data
                : '',
            generationParams,
            undefined,
            sessionId ?? undefined,
            actionId,
            userMessage
        );
    };

    const settingsPanel = (
        <VStack gap={4} align="stretch">
            {/* Width and Height */}
            <HStack gap={4}>
                <VStack align="flex-start" gap={1} flex={1}>
                    <Text fontSize="sm" fontWeight="medium" color="gray.300">Width</Text>
                    <Input
                        type="number"
                        value={generationParams.width}
                        onChange={(e) => {
                            const value = parseInt(e.target.value) || 512;
                            setGenerationParams(prev => ({ ...prev, width: value }));
                        }}
                        min={64}
                        max={2048}
                        step={64}
                        size="sm"
                        bg="gray.700"
                        borderColor="gray.600"
                        color="gray.100"
                        _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                    />
                </VStack>

                <VStack align="flex-start" gap={1} flex={1}>
                    <Text fontSize="sm" fontWeight="medium" color="gray.300">Height</Text>
                    <Input
                        type="number"
                        value={generationParams.height}
                        onChange={(e) => {
                            const value = parseInt(e.target.value) || 512;
                            setGenerationParams(prev => ({ ...prev, height: value }));
                        }}
                        min={64}
                        max={2048}
                        step={64}
                        size="sm"
                        bg="gray.700"
                        borderColor="gray.600"
                        color="gray.100"
                        _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                    />
                </VStack>
            </HStack>

            {/* Number of Frames */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Number of Frames</Text>
                <Input
                    type="number"
                    value={generationParams.num_frames}
                    onChange={(e) => {
                        const value = parseInt(e.target.value) || 49;
                        setGenerationParams(prev => ({ ...prev, num_frames: value }));
                    }}
                    min={1}
                    max={200}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Inference Steps */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Inference Steps</Text>
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
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Seed */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Seed (0 = random)</Text>
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
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'purple.500', bg: 'gray.700' }}
                />
            </VStack>

            <Button
                size="sm"
                variant="outline"
                onClick={resetToDefaults}
                w="100%"
                color="gray.200"
                borderColor="gray.600"
                _hover={{ bg: 'gray.700', borderColor: 'gray.500' }}
            >
                Reset to Defaults
            </Button>
        </VStack>
    );

    const config: ChatComponentConfig = {
        title: 'Text to Video',
        description: 'Describe the video you want to generate, and I\'ll create it for you. Each prompt will generate a new video.',
        allowedInputTypes: ['text'],
        expectedOutputType: 'video',
        placeholder: 'Describe the video you want to generate...',
        emptyStateTitle: 'Text to Video',
        emptyStateDescription: 'Describe the video you want to generate, and I\'ll create it for you. Each prompt will generate a new video.',
        emptyStateExamples: [
            'A cat playing in a garden',
            'A sunset over the ocean',
            'A futuristic city with flying cars',
            'A peaceful forest scene with birds',
        ],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
    };

    return <ChatComponent config={config} />;
};

export default Text2Video;

