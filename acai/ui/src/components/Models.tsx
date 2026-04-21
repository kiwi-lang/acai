import { useEffect, useState } from 'react';
import { Box, VStack, HStack, Text, Heading, Badge, Spinner, SimpleGrid, Button } from '@chakra-ui/react';
import { acaiAPI } from '../services/api';

interface LoadedModel {
    memory_usage: number;
    load_time: number;
}

interface LoadedModelsResponse {
    system: {
        gpu: Record<string, {
            memory: [number, number]; // [used, total]
        }>;
    };
    torch: {
        allocated: number; // MB
        reserved: number; // MB
    };
    models: Record<string, LoadedModel>;
}

const Models = () => {
    const [loadedModelsData, setLoadedModelsData] = useState<LoadedModelsResponse | null>(null);
    const [isLoadingLoaded, setIsLoadingLoaded] = useState(true);
    const [notification, setNotification] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    useEffect(() => {
        document.title = 'AI Models - Açaí';
        loadLoadedModels();
    }, []);

    const loadLoadedModels = async () => {
        try {
            const data = await acaiAPI.getLoadedModels();
            setLoadedModelsData(data);
        } catch (err) {
            console.error('Error loading loaded models:', err);
        } finally {
            setIsLoadingLoaded(false);
        }
    };

    const handleRemoveModel = async (name: string) => {
        try {
            await acaiAPI.removeLoadedModel(name);
            setNotification({ message: `Model ${name} has been freed from memory`, type: 'success' });
            setTimeout(() => setNotification(null), 3000);
            // Refresh loaded models list
            await loadLoadedModels();
        } catch (err) {
            setNotification({ message: `Failed to remove model ${name}`, type: 'error' });
            setTimeout(() => setNotification(null), 3000);
            console.error('Error removing model:', err);
        }
    };

    const formatMemory = (mb: number): string => {
        if (mb < 0) return 'N/A';
        const gb = mb / 1024;
        if (gb >= 1) {
            return `${gb.toFixed(2)} GB`;
        }
        return `${mb.toFixed(2)} MB`;
    };

    const formatTime = (seconds: number): string => {
        if (seconds < 0) return 'N/A';
        if (seconds < 1) {
            return `${(seconds * 1000).toFixed(0)} ms`;
        }
        return `${seconds.toFixed(2)} s`;
    };

    const loadedModelNames = loadedModelsData?.models ? Object.keys(loadedModelsData.models) : [];

    // Get GPU memory info (use first GPU if available)
    const gpuMemory = loadedModelsData?.system?.gpu
        ? Object.values(loadedModelsData.system.gpu)[0]?.memory
        : null;
    const memoryAvailable = gpuMemory ? gpuMemory[1] : 0; // total
    const memoryUsed = gpuMemory ? gpuMemory[0] : 0; // used
    const torchAllocated = loadedModelsData?.torch?.allocated || 0;
    const torchReserved = loadedModelsData?.torch?.reserved || 0;

    return (
        <Box p={8} maxW="7xl" mx="auto" bg="gray.900" minH="100vh">
            {notification && (
                <Box
                    p={4}
                    mb={4}
                    borderRadius="md"
                    bg={notification.type === 'success' ? 'green.900' : 'red.900'}
                    border="1px solid"
                    borderColor={notification.type === 'success' ? 'green.600' : 'red.600'}
                >
                    <Text color="white">{notification.message}</Text>
                </Box>
            )}
            <VStack align="flex-start" gap={6} mb={8}>
                <HStack justify="space-between" w="100%">
                    <VStack align="flex-start" gap={2}>
                        <Heading size="2xl" color="white">AI Models</Heading>
                        <Text fontSize="lg" color="gray.400">
                            Manage models currently loaded in memory (RAM). View resource usage statistics and free models when needed.
                        </Text>
                    </VStack>
                    <Button
                        onClick={loadLoadedModels}
                        colorScheme="green"
                        variant="outline"
                        size="sm"
                        loading={isLoadingLoaded}
                        color="gray.200"
                        borderColor="gray.600"
                        _hover={{ bg: 'gray.700', borderColor: 'gray.500', color: 'white' }}
                        px={4}
                        py={2}
                    >
                        Refresh
                    </Button>
                </HStack>

                {/* Memory Statistics */}
                <Box
                    w="100%"
                    p={6}
                    bg="gray.800"
                    borderRadius="lg"
                    border="1px solid"
                    borderColor="gray.700"
                >
                    <Heading size="md" color="white" mb={4}>Memory Statistics</Heading>
                    <SimpleGrid columns={{ base: 1, md: 2, lg: 4 }} gap={4}>
                        <Box>
                            <Text fontSize="sm" color="gray.400" mb={1}>Memory Available</Text>
                            <Text fontSize="lg" color="green.300" fontWeight="bold">
                                {formatMemory(memoryAvailable)}
                            </Text>
                        </Box>
                        <Box>
                            <Text fontSize="sm" color="gray.400" mb={1}>Memory Used</Text>
                            <Text fontSize="lg" color="yellow.300" fontWeight="bold">
                                {formatMemory(memoryUsed)}
                            </Text>
                        </Box>
                        <Box>
                            <Text fontSize="sm" color="gray.400" mb={1}>PyTorch Allocated</Text>
                            <Text fontSize="lg" color="blue.300" fontWeight="bold">
                                {formatMemory(torchAllocated)}
                            </Text>
                        </Box>
                        <Box>
                            <Text fontSize="sm" color="gray.400" mb={1}>PyTorch Reserved</Text>
                            <Text fontSize="lg" color="purple.300" fontWeight="bold">
                                {formatMemory(torchReserved)}
                            </Text>
                        </Box>
                    </SimpleGrid>
                </Box>

                {isLoadingLoaded ? (
                    <Box w="100%" p={8} textAlign="center">
                        <Spinner size="lg" color="green.500" />
                    </Box>
                ) : loadedModelNames.length === 0 ? (
                    <Box
                        w="100%"
                        p={12}
                        textAlign="center"
                        bg="gray.800"
                        borderRadius="lg"
                        border="1px solid"
                        borderColor="gray.700"
                    >
                        <Text fontSize="lg" color="gray.400">
                            No models currently loaded in memory
                        </Text>
                    </Box>
                ) : (
                    <SimpleGrid columns={{ base: 1, md: 2, lg: 3 }} gap={4} w="100%">
                        {loadedModelNames.map((name) => {
                            const model = loadedModelsData!.models[name];
                            return (
                                <Box
                                    key={name}
                                    p={5}
                                    bg="gray.800"
                                    borderRadius="lg"
                                    border="1px solid"
                                    borderColor="green.600"
                                    _hover={{
                                        shadow: 'lg',
                                        borderColor: 'green.400',
                                    }}
                                    transition="all 0.2s"
                                >
                                    <VStack align="flex-start" gap={3}>
                                        <HStack justify="space-between" w="100%">
                                            <Text fontSize="md" fontWeight="bold" color="white" truncate maxW="70%">
                                                {name}
                                            </Text>
                                            <Badge colorScheme="green" fontSize="xs">
                                                Loaded
                                            </Badge>
                                        </HStack>

                                        <VStack align="flex-start" gap={2} w="100%" fontSize="sm">
                                            <HStack justify="space-between" w="100%">
                                                <Text color="gray.400">Memory Usage:</Text>
                                                <Text color="green.300" fontWeight="semibold">
                                                    {formatMemory(model.memory_usage)}
                                                </Text>
                                            </HStack>
                                            <HStack justify="space-between" w="100%">
                                                <Text color="gray.400">Load Time:</Text>
                                                <Text color="blue.300" fontWeight="semibold">
                                                    {formatTime(model.load_time)}
                                                </Text>
                                            </HStack>
                                        </VStack>

                                        <Button
                                            size="sm"
                                            colorScheme="red"
                                            variant="outline"
                                            w="100%"
                                            onClick={() => handleRemoveModel(name)}
                                            color="red.300"
                                            borderColor="red.600"
                                            _hover={{ bg: 'red.900', borderColor: 'red.400', color: 'white' }}
                                            px={4}
                                            py={2}
                                        >
                                            Free from Memory
                                        </Button>
                                    </VStack>
                                </Box>
                            );
                        })}
                    </SimpleGrid>
                )}
            </VStack>
        </Box>
    );
};

export default Models;

