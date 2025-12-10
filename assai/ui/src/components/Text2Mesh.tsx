import { useState } from 'react';
import { VStack, Input, Button, HStack, Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import { Message } from '../services/types';
import { assaiAPI, MeshGenerationParams } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Text2Mesh = () => {
    const { sessionId } = useWebSocket();

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<MeshGenerationParams>({
        guidance_scale: 3.0,
        num_inference_steps: 50,
        seed: 0,
    });

    const resetToDefaults = () => {
        setGenerationParams({
            guidance_scale: 3.0,
            num_inference_steps: 50,
            seed: 0,
        });
    };

    const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
        return await assaiAPI.generateMesh(
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
            {/* Guidance Scale */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">
                    Guidance Scale: {generationParams.guidance_scale?.toFixed(1)}
                </Text>
                <Input
                    type="number"
                    value={generationParams.guidance_scale}
                    onChange={(e) => {
                        const value = parseFloat(e.target.value) || 3.0;
                        setGenerationParams(prev => ({ ...prev, guidance_scale: value }));
                    }}
                    min={1.0}
                    max={10.0}
                    step={0.1}
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
        title: 'Text to 3D Mesh',
        description: 'Describe the 3D model you want to generate, and I\'ll create it for you. Each prompt will generate a new 3D mesh.',
        allowedInputTypes: ['text'],
        expectedOutputType: 'mesh',
        placeholder: 'Describe the 3D model you want to generate...',
        emptyStateTitle: 'Text to 3D Mesh',
        emptyStateDescription: 'Describe the 3D model you want to generate, and I\'ll create it for you. Each prompt will generate a new 3D mesh.',
        emptyStateExamples: [
            'A cute cat',
            'A vintage car',
            'A modern chair',
            'A fantasy sword',
        ],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
    };

    return <ChatComponent config={config} />;
};

export default Text2Mesh;

