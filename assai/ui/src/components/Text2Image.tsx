import { useState, useEffect } from 'react';
import { VStack, Input, Button, HStack, Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import { Message } from '../services/types';
import { assaiAPI, ImageGenerationParams } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Text2Image = () => {
    const { sessionId } = useWebSocket();
    const [availableModels, setAvailableModels] = useState<string[]>([]);
    const [selectedModel, setSelectedModel] = useState<string | null>(null);
    const [customModel, setCustomModel] = useState<string>('');
    const [useCustomModel, setUseCustomModel] = useState<boolean>(false);
    const [isLoadingModels, setIsLoadingModels] = useState<boolean>(true);

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<ImageGenerationParams>({
        height: 256,
        width: 256,
        guidance_scale: 3.5,
        num_inference_steps: 50,
        max_sequence_length: 512,
        seed: 0,
    });

    // Fetch available models on mount
    useEffect(() => {
        const fetchModels = async () => {
            try {
                setIsLoadingModels(true);
                const models = await assaiAPI.listText2ImageModels();
                setAvailableModels(models);
                if (models.length > 0 && !selectedModel) {
                    setSelectedModel(models[0]);
                }
            } catch (error) {
                console.error('Failed to fetch models:', error);
            } finally {
                setIsLoadingModels(false);
            }
        };
        fetchModels();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

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

    const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
        // Determine which model to use
        const modelToUse = useCustomModel && customModel.trim()
            ? customModel.trim()
            : selectedModel || undefined;

        return await assaiAPI.generateImage(
            typeof userMessage.content === 'object' && userMessage.content.kind === 'text'
                ? userMessage.content.data
                : '',
            generationParams,
            modelToUse,
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
                            const value = parseInt(e.target.value) || 256;
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
                            const value = parseInt(e.target.value) || 256;
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

            {/* Guidance Scale */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">
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

            {/* Max Sequence Length */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Max Sequence Length</Text>
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

    // Model selector component
    const modelSelector = (
        <HStack gap={3} align="center" w="100%" maxW="48rem" mx="auto">
            <Text fontSize="sm" fontWeight="medium" color="gray.300" minW="fit-content">
                Model:
            </Text>
            <select
                value={useCustomModel ? 'custom' : (selectedModel || '')}
                onChange={(e: React.ChangeEvent<HTMLSelectElement>) => {
                    if (e.target.value === 'custom') {
                        setUseCustomModel(true);
                    } else {
                        setUseCustomModel(false);
                        setSelectedModel(e.target.value);
                    }
                }}
                disabled={isLoadingModels}
                style={{
                    flex: 1,
                    maxWidth: '300px',
                    padding: '6px 12px',
                    borderRadius: '6px',
                    border: '1px solid #4A5568',
                    fontSize: '14px',
                    backgroundColor: '#2D3748',
                    color: '#E2E8F0',
                    cursor: isLoadingModels ? 'not-allowed' : 'pointer',
                }}
            >
                {availableModels.map((model) => (
                    <option key={model} value={model} style={{ backgroundColor: '#1a202c', color: '#e2e8f0' }}>
                        {model}
                    </option>
                ))}
                <option value="custom" style={{ backgroundColor: '#1a202c', color: '#e2e8f0' }}>
                    Custom Model...
                </option>
            </select>
            {useCustomModel && (
                <Input
                    placeholder="Enter HuggingFace model name (e.g., black-forest-labs/FLUX.1-dev)"
                    value={customModel}
                    onChange={(e) => setCustomModel(e.target.value)}
                    size="sm"
                    bg="gray.800"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'purple.500', bg: 'gray.800' }}
                    flex={1}
                    maxW="400px"
                />
            )}
        </HStack>
    );

    const config: ChatComponentConfig = {
        title: 'Text to Image',
        description: 'Describe the image you want to generate, and I\'ll create it for you. Each prompt will generate a new image.',
        allowedInputTypes: ['text'],
        expectedOutputType: 'image',
        placeholder: 'Describe the image you want to generate...',
        emptyStateTitle: 'Text to Image',
        emptyStateDescription: 'Describe the image you want to generate, and I\'ll create it for you. Each prompt will generate a new image.',
        emptyStateExamples: [
            'A serene sunset over mountains',
            'A futuristic cityscape at night',
            'A cute cat playing with yarn',
            'An abstract painting with vibrant colors',
        ],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
        modelSelector,
    };

    return <ChatComponent config={config} />;
};

export default Text2Image;
