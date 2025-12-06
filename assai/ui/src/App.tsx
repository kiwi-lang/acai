import { HashRouter as Router, Routes, Route } from 'react-router-dom';
import {
  ChakraProvider,
  createSystem,
  defaultConfig
} from '@chakra-ui/react';
import Layout from './layout/Layout';
import Home from './components/Home';
import ApiTester from './components/ApiTester';
import Models from './components/Models';
import './App.css';

// Create the theme system for Chakra UI v3
const system = createSystem(defaultConfig);

function App() {
  return (
    <ChakraProvider value={system}>
      <Router>
        <Layout>
          <Routes>
            <Route path="/" element={<Home />} />
            <Route path="/chat/:conversationId" element={<Home />} />
            <Route path="/models" element={<Models />} />
            <Route path="/api-tester" element={<ApiTester />} />
          </Routes>
        </Layout>
      </Router>
    </ChakraProvider>
  );
}

export default App;
