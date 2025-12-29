import { useState } from 'react';
import { VStack, Button, Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import { Message } from '../services/types';
import { assaiAPI, DepthEstimationParams } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const DepthEstimation = () => {
    const { sessionId } = useWebSocket();

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<DepthEstimationParams>({
        colormap: 'jet',
    });

    const resetToDefaults = () => {
        setGenerationParams({
            colormap: 'jet',
        });
    };

    const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
        // Extract image data URL from message
        const imageDataUrl = typeof userMessage.content === 'object' && userMessage.content.kind === 'image'
            ? userMessage.content.data
            : '';

        return await assaiAPI.estimateDepth(
            imageDataUrl,
            generationParams,
            undefined,
            sessionId ?? undefined,
            actionId,
            userMessage
        );
    };

    const settingsPanel = (
        <VStack gap={4} align="stretch">
            {/* Colormap Selection */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Colormap</Text>
                <select
                    value={generationParams.colormap}
                    onChange={(e) => {
                        setGenerationParams(prev => ({ ...prev, colormap: e.target.value as any }));
                    }}
                    style={{
                        width: '100%',
                        padding: '8px',
                        borderRadius: '6px',
                        border: '1px solid #4A5568',
                        fontSize: '14px',
                        backgroundColor: '#2D3748',
                        color: '#E2E8F0',
                    }}
                >
                    <option value="jet" style={{ background: '#1A202C' }}>Jet</option>
                    <option value="viridis" style={{ background: '#1A202C' }}>Viridis</option>
                    <option value="plasma" style={{ background: '#1A202C' }}>Plasma</option>
                    <option value="inferno" style={{ background: '#1A202C' }}>Inferno</option>
                    <option value="magma" style={{ background: '#1A202C' }}>Magma</option>
                    <option value="turbo" style={{ background: '#1A202C' }}>Turbo</option>
                </select>
                <Text fontSize="xs" color="gray.500" mt={1}>
                    Choose a colormap to visualize depth. Jet provides classic depth visualization,
                    while Viridis/Plasma are perceptually uniform.
                </Text>
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
        title: 'Depth Estimation',
        description: 'Upload an image and I\'ll estimate the depth map. The output will be a colored image where colors represent estimated depth.',
        allowedInputTypes: ['image'],
        expectedOutputType: 'image',
        placeholder: 'Upload an image to estimate depth...',
        emptyStateTitle: 'Depth Estimation',
        emptyStateDescription: 'Upload an image and I\'ll estimate the depth map. The output will be a colored image where colors represent estimated depth.',
        emptyStateExamples: [],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
    };

    return <ChatComponent config={config} />;
};

export default DepthEstimation;

