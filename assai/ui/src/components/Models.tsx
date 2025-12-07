import { useEffect, useState } from 'react';
import { Box, VStack, HStack, Text, Heading, Badge, Spinner, SimpleGrid } from '@chakra-ui/react';
import { assaiAPI } from '../services/api';
import { ModelPlugin } from '../services/types';

const ModelCard = ({ model }: { model: ModelPlugin }) => {
    const getColorScheme = (type: string) => {
        const colors: Record<string, string> = {
            'text2text': 'blue',
            'text2image': 'purple',
            'text2speech': 'green',
            'text2audio': 'teal',
            'image2text': 'orange',
            'speech2text': 'pink',
        };
        return colors[type] || 'gray';
    };

    return (
        <Box
            p={6}
            bg="gray.800"
            borderRadius="lg"
            border="1px solid"
            borderColor="gray.700"
            _hover={{
                shadow: 'lg',
                borderColor: 'green.300',
            }}
            transition="all 0.2s"
        >
            <VStack align="flex-start" gap={3}>
                <HStack justify="space-between" w="100%">
                    <Text fontSize="lg" fontWeight="bold" color="white">
                        {model.name}
                    </Text>
                    <Badge colorScheme={getColorScheme(model.type)} fontSize="xs">
                        {model.type}
                    </Badge>
                </HStack>

                <HStack gap={2} fontSize="sm" color="gray.300">
                    <Badge variant="subtle" colorScheme="blue">
                        {model.input}
                    </Badge>
                    <Text>→</Text>
                    <Badge variant="subtle" colorScheme="green">
                        {model.output}
                    </Badge>
                </HStack>

                {model.description && (
                    <Text fontSize="sm" color="gray.400">
                        {model.description}
                    </Text>
                )}
            </VStack>
        </Box>
    );
};

const Models = () => {
    const [models, setModels] = useState<ModelPlugin[]>([]);
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        document.title = 'Models - ASSAI';
        loadModels();
    }, []);

    const loadModels = async () => {
        try {
            const data = await assaiAPI.getModels();
            setModels(data);
        } catch (err) {
            setError('Failed to load models');
            console.error('Error loading models:', err);
        } finally {
            setIsLoading(false);
        }
    };

    if (isLoading) {
        return (
            <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                h="100vh"
                bg="gray.900"
            >
                <VStack gap={4}>
                    <Spinner size="xl" color="green.500" />
                    <Text color="gray.300">Loading models...</Text>
                </VStack>
            </Box>
        );
    }

    if (error) {
        return (
            <Box
                display="flex"
                alignItems="center"
                justifyContent="center"
                h="100vh"
                bg="gray.900"
            >
                <VStack gap={4}>
                    <Text color="red.400" fontSize="lg">
                        {error}
                    </Text>
                </VStack>
            </Box>
        );
    }

    return (
        <Box p={8} maxW="7xl" mx="auto" bg="gray.900" minH="100vh">
            <VStack align="flex-start" gap={6} mb={8}>
                <Heading size="2xl" color="white">AI Models</Heading>
                <Text fontSize="lg" color="gray.400">
                    Available AI model plugins in ASSAI. These models power various capabilities
                    from text generation to image creation and speech processing.
                </Text>
            </VStack>

            {models.length === 0 ? (
                <Box
                    p={12}
                    textAlign="center"
                    bg="gray.800"
                    borderRadius="lg"
                    border="1px solid"
                    borderColor="gray.700"
                >
                    <Text fontSize="lg" color="gray.400">
                        No models currently loaded. Add model plugins to get started.
                    </Text>
                </Box>
            ) : (
                <SimpleGrid columns={{ base: 1, md: 2, lg: 3 }} gap={6}>
                    {models.map((model) => (
                        <ModelCard key={model.name} model={model} />
                    ))}
                </SimpleGrid>
            )}
        </Box>
    );
};

export default Models;

