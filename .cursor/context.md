* assai/server
    * core definition of flask app
* assai/agent
    * Compute server
* assai/models
    * Each module is a AI model plugin
    * The plugins are loaded by the flask server
    * Plugins define custom routes
* assai/ui
    * TypeScript React UI
    * use ChakraUI library
* assai/ui/src/services/api.ts
    * All the AJAX calls made by the frontend to the flask server
    * The frontend only calls method inside api.ts
    * api.ts is the only location that interacts with the backend
* assai/ui/src/services/types.ts
    * Definition of the types returned by the backend
* tests
    * backend tests
* docs
    * sphinx documentation for both front and backend


    React Front End <-> Flask Backend <-> Compute Backend


* Flask Backend and Compute Backend can be the samething 
  but it does not have to be
