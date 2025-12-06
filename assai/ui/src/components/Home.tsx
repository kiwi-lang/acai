import { useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Box, VStack, Text, Button, HStack, Heading } from '@chakra-ui/react';

const Home = () => {

  useEffect(() => {
    document.title = 'RecipeBook';
  }, []);

  return (
    <Box py={10}>
      <VStack gap={8} align="center" maxW="4xl" mx="auto">
        <Box textAlign="center">
          <Heading size="2xl" mb={4}>
            Welcome to RecipeBook
          </Heading>
          <Text fontSize="xl" color="gray.600" mb={8}>
          </Text>
        </Box>


        <HStack gap={6} flexWrap="wrap" justify="center">
          <Link to="/recipes">
            <Button colorScheme="blue" size="lg">
              Browse All Recipes
            </Button>
          </Link>

          <Link to="/ingredients">
            <Button colorScheme="purple" variant="outline" size="lg">
              View Ingredients
            </Button>
          </Link>
        </HStack>

        {/* Feature highlights */}
        <VStack gap={4} mt={8} maxW="2xl" textAlign="center">
          <Heading size="md" color="gray.700">
          </Heading>
          <VStack gap={2} fontSize="sm" color="gray.600">
          </VStack>
        </VStack>
      </VStack>
    </Box>
  );
};

export default Home;