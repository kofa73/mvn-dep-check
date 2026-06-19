# POM Analyzer

This script deterministically analyzes a Maven project's `pom.xml` and outputs a structured JSON report to help you safely optimize your dependencies, specifically targeting Spring Boot projects.

## JSON Output Categories

When the script runs, it outputs a JSON object with the following four categories. Here is how you or your AI assistant should interpret and act upon them:

### 1. `manual_review_required`
Dependencies in this list were explicitly declared in your `pom.xml` but Maven's compile-time analysis (`mvn dependency:analyze`) could not find any direct usage in your source code. 
* **Action to take:** Do **NOT** automatically delete these. They may be required dynamically at runtime (e.g., via reflection, SPIs, JDBC drivers, or auto-configuration). Consider changing their `<scope>` to `runtime` and injecting a `<!-- TODO: check if really needed -->` comment above them to safely shrink your compile-time classpath without risking runtime crashes.

### 2. `hitlist_exclusions`
Heavyweight transitive dependencies (configured in `heavy-dependencies.json`) that are present in your dependency graph, but for which zero usage evidence could be found in your `.java`, `.kt`, `.yaml`, `.yml`, or `.properties` files.
* **Action to take:** Find the top-level parent dependency (e.g., a massive Cloud Provider starter) that is pulling this library into the project. Add an explicit `<exclusion>` block to that parent dependency in your `pom.xml` to prevent the unused library from bloating your final application package.

### 3. `redundant_versions`
Explicit `<version>` tags in your `pom.xml` that perfectly match the default version already provided by your project's Spring Boot BOM (`spring-boot-dependencies`).
* **Action to take:** Surgically delete these explicit `<version>` tags. They are redundant noise and prevent Spring Boot from seamlessly upgrading the library automatically when you bump your Spring Boot version.

### 4. `overridden_versions`
Explicit `<version>` tags in your `pom.xml` that differ from the default version provided by the Spring Boot BOM.
* **Action to take:** Keep these versions intact, as they are intentionally overriding Spring Boot defaults (often for security patches or compatibility). However, you should inject an XML comment above them documenting the reason for the override, e.g., `<!-- TODO: specify why overridden; inherited version is x.y.z -->`.
