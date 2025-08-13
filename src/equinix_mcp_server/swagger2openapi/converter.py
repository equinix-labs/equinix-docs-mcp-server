import copy
import json
import re

def _walk_schema(schema, action):
    """
    Recursively walks a schema and applies an action.
    """
    action(schema)
    if "items" in schema:
        _walk_schema(schema["items"], action)
    if "properties" in schema:
        for prop_name, prop_schema in schema["properties"].items():
            _walk_schema(prop_schema, action)
    if "allOf" in schema:
        for sub_schema in schema["allOf"]:
            _walk_schema(sub_schema, action)
    if "anyOf" in schema:
        for sub_schema in schema["anyOf"]:
            _walk_schema(sub_schema, action)
    if "oneOf" in schema:
        for sub_schema in schema["oneOf"]:
            _walk_schema(sub_schema, action)
    if "not" in schema:
        _walk_schema(schema["not"], action)


def _recurse(obj, action):
    """
    Recursively walks a dictionary/list structure and applies an action.
    """
    if isinstance(obj, dict):
        action(obj)
        for key, value in obj.items():
            _recurse(value, action)
    elif isinstance(obj, list):
        for item in obj:
            _recurse(item, action)


class Swagger2OpenAPIConverter:
    """
    Faithful Python port of the core logic from swagger2openapi/index.js.
    Handles security schemes, parameters, responses, schemas, extensions, and basic $ref rewriting.
    """

    def __init__(self):
        self.options = {}

    def _fix_up_sub_schema(self, schema):
        """Ports the fixUpSubSchema function."""
        if schema.get("discriminator") and isinstance(schema.get("discriminator"), str):
            schema["discriminator"] = {"propertyName": schema["discriminator"]}
        if schema.get("type") == "file":
            schema["type"] = "string"
            schema["format"] = "binary"
        if "allowEmptyValue" in schema:
            del schema["allowEmptyValue"]
        if schema.get("type") == "null":
            del schema["type"]
            schema["nullable"] = True
        if "x-nullable" in schema:
            schema["nullable"] = schema.pop("x-nullable")

    def _fix_up_schema(self, schema):
        """Ports the fixUpSchema function."""
        _walk_schema(schema, self._fix_up_sub_schema)

    def _rewrite_ref(self, ref):
        """Rewrites a Swagger $ref to an OpenAPI $ref."""
        if ref.startswith("#/definitions/"):
            return f"#/components/schemas/{ref[len('#/definitions/'):]}"
        if ref.startswith("#/parameters/"):
            return f"#/components/parameters/{ref[len('#/parameters/'):]}"
        if ref.startswith("#/responses/"):
            return f"#/components/responses/{ref[len('#/responses/'):]}"
        return ref

    def _fixup_refs(self, obj):
        """Ports the fixupRefs function."""
        if isinstance(obj, dict) and "$ref" in obj:
            obj["$ref"] = self._rewrite_ref(obj["$ref"])

    def _convert_security_definitions(self, secdefs):
        """Converts security definitions."""
        for name, scheme in secdefs.items():
            if scheme.get("type") == "basic":
                scheme["type"] = "http"
                scheme["scheme"] = "basic"
            elif scheme.get("type") == "oauth2":
                flow = scheme.pop("flow")
                flows = {}
                if flow == "implicit":
                    flows["implicit"] = {
                        "authorizationUrl": scheme.pop("authorizationUrl"),
                        "scopes": scheme.pop("scopes"),
                    }
                elif flow == "password":
                    flows["password"] = {
                        "tokenUrl": scheme.pop("tokenUrl"),
                        "scopes": scheme.pop("scopes"),
                    }
                elif flow == "application":
                    flows["clientCredentials"] = {
                        "tokenUrl": scheme.pop("tokenUrl"),
                        "scopes": scheme.pop("scopes"),
                    }
                elif flow == "accessCode":
                    flows["authorizationCode"] = {
                        "authorizationUrl": scheme.pop("authorizationUrl"),
                        "tokenUrl": scheme.pop("tokenUrl"),
                        "scopes": scheme.pop("scopes"),
                    }
                scheme["flows"] = flows
            elif scheme.get("type") == "apiKey":
                # apiKey maps directly; ensure required fields are present
                # Swagger uses 'name' and 'in' which are the same in OAS3
                pass
        return secdefs

    def _convert_operation(self, op, openapi):
        """Converts a single operation."""
        consumes = op.get("consumes", openapi.get("consumes", []))
        produces = op.get("produces", openapi.get("produces", []))

        # Parameters
        if "parameters" in op:
            form_data_params = [p for p in op["parameters"] if p.get("in") == "formData"]
            body_param = next((p for p in op["parameters"] if p.get("in") == "body"), None)

            if body_param:
                op["requestBody"] = {"content": {}}
                if body_param.get("description"):
                    op["requestBody"]["description"] = body_param["description"]
                if body_param.get("required"):
                    op["requestBody"]["required"] = body_param["required"]
                for content_type in consumes:
                    op["requestBody"]["content"][content_type] = {
                        "schema": body_param.get("schema", {})
                    }

            if form_data_params:
                if "requestBody" not in op:
                    op["requestBody"] = {"content": {}}
                
                content_type = "application/x-www-form-urlencoded"
                if "multipart/form-data" in consumes:
                    content_type = "multipart/form-data"

                if content_type not in op["requestBody"]["content"]:
                    op["requestBody"]["content"][content_type] = {
                        "schema": {"type": "object", "properties": {}}
                    }
                
                schema = op["requestBody"]["content"][content_type]["schema"]
                required_fields = schema.get("required", [])

                for param in form_data_params:
                    prop_name = param["name"]
                    prop_schema = {k: v for k, v in param.items() if k not in ["name", "in", "required"]}
                    if param.get("type") == "file":
                        prop_schema["type"] = "string"
                        prop_schema["format"] = "binary"
                    schema["properties"][prop_name] = prop_schema
                    if param.get("required"):
                        required_fields.append(prop_name)
                
                if required_fields:
                    schema["required"] = required_fields

            op["parameters"] = [
                self._convert_parameter(p) 
                for p in op["parameters"] 
                if p.get("in") not in ["body", "formData"]
            ]

        # Responses
        if "responses" in op:
            for code, response in op["responses"].items():
                if "schema" in response:
                    schema = response.pop("schema")
                    response["content"] = {}
                    for content_type in produces:
                        response["content"][content_type] = {"schema": schema}
                if "headers" in response:
                    for name, header in response["headers"].items():
                        if "type" in header:
                            header["schema"] = {"type": header.pop("type")}
                            if "format" in header:
                                header["schema"]["format"] = header.pop("format")

    def _convert_parameter(self, param):
        """Converts a single Swagger 2.0 parameter to OpenAPI 3.0."""
        # This handles dereferenced parameters
        if "schema" not in param and "type" in param:
            schema = {}
            # List of properties to move into the schema object
            schema_properties = [
                "type", "format", "items", "default", "maximum", 
                "exclusiveMaximum", "minimum", "exclusiveMinimum", 
                "maxLength", "minLength", "pattern", "maxItems", "minItems", 
                "uniqueItems", "enum", "multipleOf", "collectionFormat"
            ]
            for key in schema_properties:
                if key in param:
                    schema[key] = param.pop(key)
            param["schema"] = schema
        return param

    def convert(self, swagger):
        """Main conversion method."""
        openapi = copy.deepcopy(swagger)

        openapi["openapi"] = "3.0.0"
        openapi.pop("swagger", None)

        # Convert host/basePath to servers
        servers = []
        scheme = "https"
        if "schemes" in swagger and swagger["schemes"]:
            scheme = swagger["schemes"][0]
        
        if "host" in swagger:
            url = f"{scheme}://{swagger['host']}"
            if "basePath" in swagger:
                url += swagger["basePath"]
            servers.append({"url": url})
        
        if servers:
            openapi["servers"] = servers
        
        openapi.pop("host", None)
        openapi.pop("basePath", None)
        openapi.pop("schemes", None)

        # Basic info
        if "info" not in openapi:
            openapi["info"] = {"title": "Converted API", "version": "1.0.0"}

        # Create components object
        openapi["components"] = openapi.get("components", {})

        # Move top-level objects to components
        for component_type in ["parameters", "responses"]:
            if component_type in openapi:
                openapi["components"][component_type] = openapi.pop(component_type)

        if "definitions" in openapi:
            openapi["components"]["schemas"] = openapi.pop("definitions")
        
        if "securityDefinitions" in openapi:
            openapi["components"]["securitySchemes"] = self._convert_security_definitions(
                openapi.pop("securityDefinitions")
            )

        # Promote security to root: if Swagger had top-level security, carry it over.
        # Otherwise, if there are securitySchemes defined but no top-level security,
        # set a sensible default so auth applies by default to all endpoints.
        # Operation-level "security" (including empty lists) will override this.
        if "security" in swagger:
            # Copy as-is; structure is compatible between Swagger 2.0 and OAS3
            openapi["security"] = copy.deepcopy(swagger["security"])
        else:
            schemes = openapi.get("components", {}).get("securitySchemes", {})
            if schemes:
                # If exactly one scheme, use that. If multiple, declare each as an alternative (OR).
                default_reqs = []
                for scheme_name in schemes.keys():
                    default_reqs.append({scheme_name: []})
                openapi["security"] = default_reqs

        # Process all schemas
        if "schemas" in openapi["components"]:
            for schema_name, schema in openapi["components"]["schemas"].items():
                self._fix_up_schema(schema)

        # Process paths
        for path, path_item in openapi.get("paths", {}).items():
            for method, op in path_item.items():
                if method.lower() in ["get", "put", "post", "delete", "options", "head", "patch", "trace"]:
                    self._convert_operation(op, openapi)

        # Final pass to fix all $refs
        _recurse(openapi, self._fixup_refs)

        openapi.pop("consumes", None)
        openapi.pop("produces", None)

        return openapi


# Example usage
if __name__ == "__main__":
    # This part is for standalone testing and can be removed or adapted
    try:
        with open("swagger.json", "r", encoding="utf-8") as f:
            swagger_spec = json.load(f)

        converter = Swagger2OpenAPIConverter()
        openapi_spec = converter.convert(swagger_spec)

        with open("openapi.json", "w", encoding="utf-8") as f:
            json.dump(openapi_spec, f, indent=2, ensure_ascii=False)
        
        print("Conversion successful: openapi.json created.")

    except FileNotFoundError:
        print("Error: swagger.json not found. Please provide a Swagger 2.0 spec file.")
    except json.JSONDecodeError:
        print("Error: Invalid JSON in swagger.json.")
    except Exception as e:
        print(f"An unexpected error occurred: {e}")
