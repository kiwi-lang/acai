import { useEffect, useState } from 'react';
import { Box, VStack, HStack, Text, Heading, Badge, Spinner, SimpleGrid, Button } from '@chakra-ui/react';
import { assaiAPI } from '../services/api';

interface LoadedModel {
    memory_usage: number;
    load_time: number;
}

const Models = () => {
    const [loadedModels, setLoadedModels] = useState<Record<string, LoadedModel>>({});
    const [isLoadingLoaded, setIsLoadingLoaded] = useState(true);
    const [notification, setNotification] = useState<{ message: string; type: 'success' | 'error' } | null>(null);

    useEffect(() => {
        document.title = 'AI Models - ASSAI';
        loadLoadedModels();
    }, []);

    const loadLoadedModels = async () => {
        try {
            const data = await assaiAPI.getLoadedModels();
            setLoadedModels(data);
        } catch (err) {
            console.error('Error loading loaded models:', err);
        } finally {
            setIsLoadingLoaded(false);
        }
    };

    const handleRemoveModel = async (name: string) => {
        try {
            await assaiAPI.removeLoadedModel(name);
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

    const loadedModelNames = Object.keys(loadedModels);

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
                            const model = loadedModels[name];
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

