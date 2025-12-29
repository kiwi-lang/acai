import { useState, useEffect } from 'react';
import { VStack, Input, Button, HStack, Text } from '@chakra-ui/react';
import ChatComponent, { ChatComponentConfig } from './ChatComponent';
import { Message } from '../services/types';
import { assaiAPI, TextGenerationParams } from '../services/api';
import { useWebSocket } from '../contexts/WebSocketContext';

const Text2Text = () => {
    const { sessionId } = useWebSocket();
    const [availableModels, setAvailableModels] = useState<string[]>([]);
    const [selectedModel, setSelectedModel] = useState<string | null>(null);
    const [customModel, setCustomModel] = useState<string>('');
    const [useCustomModel, setUseCustomModel] = useState<boolean>(false);
    const [isLoadingModels, setIsLoadingModels] = useState<boolean>(true);

    // Generation parameters with defaults matching backend
    const [generationParams, setGenerationParams] = useState<TextGenerationParams>({
        max_new_tokens: 50,
        temperature: 0.7,
        top_p: 0.9,
        top_k: 50,
        repetition_penalty: 1.0,
        do_sample: true,
    });

    // Fetch available models on mount
    useEffect(() => {
        const fetchModels = async () => {
            try {
                setIsLoadingModels(true);
                const models = await assaiAPI.listText2TextModels();
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
    }, []);

    const resetToDefaults = () => {
        setGenerationParams({
            max_new_tokens: 50,
            temperature: 0.7,
            top_p: 0.9,
            top_k: 50,
            repetition_penalty: 1.0,
            do_sample: true,
        });
    };

    const handleSendMessage = async (userMessage: Message, actionId: number): Promise<{ message: Message }> => {
        // Determine which model to use
        const modelToUse = useCustomModel && customModel.trim()
            ? customModel.trim()
            : selectedModel || undefined;

        return await assaiAPI.generateText(
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
            {/* Max New Tokens */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Max New Tokens</Text>
                <Input
                    type="number"
                    value={generationParams.max_new_tokens}
                    onChange={(e) => {
                        const value = parseInt(e.target.value) || 50;
                        setGenerationParams(prev => ({ ...prev, max_new_tokens: value }));
                    }}
                    min={1}
                    max={2048}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'blue.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Temperature */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">
                    Temperature: {generationParams.temperature?.toFixed(2)}
                </Text>
                <Input
                    type="number"
                    value={generationParams.temperature}
                    onChange={(e) => {
                        const value = parseFloat(e.target.value) || 0.7;
                        setGenerationParams(prev => ({ ...prev, temperature: value }));
                    }}
                    min={0}
                    max={2}
                    step={0.1}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'blue.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Top P */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">
                    Top P: {generationParams.top_p?.toFixed(2)}
                </Text>
                <Input
                    type="number"
                    value={generationParams.top_p}
                    onChange={(e) => {
                        const value = parseFloat(e.target.value) || 0.9;
                        setGenerationParams(prev => ({ ...prev, top_p: value }));
                    }}
                    min={0}
                    max={1}
                    step={0.05}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'blue.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Top K */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Top K</Text>
                <Input
                    type="number"
                    value={generationParams.top_k}
                    onChange={(e) => {
                        const value = parseInt(e.target.value) || 50;
                        setGenerationParams(prev => ({ ...prev, top_k: value }));
                    }}
                    min={0}
                    max={100}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'blue.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Repetition Penalty */}
            <VStack align="flex-start" gap={1}>
                <Text fontSize="sm" fontWeight="medium" color="gray.300">
                    Repetition Penalty: {generationParams.repetition_penalty?.toFixed(2)}
                </Text>
                <Input
                    type="number"
                    value={generationParams.repetition_penalty}
                    onChange={(e) => {
                        const value = parseFloat(e.target.value) || 1.0;
                        setGenerationParams(prev => ({ ...prev, repetition_penalty: value }));
                    }}
                    min={0}
                    max={2}
                    step={0.1}
                    size="sm"
                    bg="gray.700"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'blue.500', bg: 'gray.700' }}
                />
            </VStack>

            {/* Do Sample */}
            <HStack justify="space-between">
                <Text fontSize="sm" fontWeight="medium" color="gray.300">Do Sample</Text>
                <Button
                    size="sm"
                    variant={generationParams.do_sample ? "solid" : "outline"}
                    colorScheme={generationParams.do_sample ? "blue" : "gray"}
                    onClick={() => {
                        setGenerationParams(prev => ({ ...prev, do_sample: !prev.do_sample }));
                    }}
                    px={3}
                >
                    {generationParams.do_sample ? "On" : "Off"}
                </Button>
            </HStack>

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
                    placeholder="Enter model name (e.g., mistralai/Mistral-7B-Instruct-v0.2)"
                    value={customModel}
                    onChange={(e) => setCustomModel(e.target.value)}
                    size="sm"
                    bg="gray.800"
                    borderColor="gray.600"
                    color="gray.100"
                    _focus={{ borderColor: 'blue.500', bg: 'gray.800' }}
                    flex={1}
                    maxW="400px"
                />
            )}
        </HStack>
    );

    const config: ChatComponentConfig = {
        title: 'Text to Text',
        description: 'Have a conversation with an AI language model. Ask questions, get answers, and explore AI-generated text.',
        allowedInputTypes: ['text'],
        expectedOutputType: 'text',
        placeholder: 'Start a conversation...',
        emptyStateTitle: 'Text to Text',
        emptyStateDescription: 'Have a conversation with an AI language model. Ask questions, get answers, and explore AI-generated text.',
        emptyStateExamples: [
            'Tell me a short story about a robot',
            'Explain quantum computing in simple terms',
            'Write a poem about the ocean',
            'What are the benefits of renewable energy?',
        ],
        onSendMessage: handleSendMessage,
        settingsPanel,
        defaultShowSettings: true,
        modelSelector,
    };

    return <ChatComponent config={config} />;
};

export default Text2Text;
