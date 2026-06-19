import os
import sys
import json
import subprocess
import xml.etree.ElementTree as ET

def run_command(cmd, cwd=None, allowed_exit_codes=None):
    if allowed_exit_codes is None:
        allowed_exit_codes = [0]
    try:
        result = subprocess.run(cmd, cwd=cwd, shell=True, text=True, capture_output=True)
        if result.returncode not in allowed_exit_codes:
            print(f"WARNING: Command failed with exit code {result.returncode}: {cmd}\nStderr: {result.stderr}", file=sys.stderr)
        return result.stdout, result.stderr, result.returncode
    except Exception as e:
        print(f"ERROR: Exception running {cmd}\n{e}", file=sys.stderr)
        return "", str(e), 1

def analyze_unused_dependencies():
    print("Running dependency:analyze...", file=sys.stderr)
    stdout, stderr, rc = run_command("mvn dependency:analyze -DignoreNonCompile=true -DoutputXML=false")
    unused = []
    in_unused_section = False
    for line in stdout.splitlines():
        line = line.strip()
        if "Unused declared dependencies found:" in line:
            in_unused_section = True
            continue
        if in_unused_section:
            if not line or line.startswith("[INFO]") and not line[6:].strip():
                continue
            if line.startswith("[WARNING]") and line != "[WARNING] Unused declared dependencies found:":
                parts = line.split()
                if len(parts) > 1:
                    dep_str = parts[-1]
                    dep_parts = dep_str.split(':')
                    if len(dep_parts) >= 2:
                        unused.append({"groupId": dep_parts[0], "artifactId": dep_parts[1]})
            elif line.startswith("[INFO]"):
                in_unused_section = False
    return unused

def check_heavy_dependencies(config_path):
    print("Checking heavy dependencies...", file=sys.stderr)
    if not os.path.exists(config_path):
        return []
    
    with open(config_path, 'r') as f:
        config = json.load(f)
        
    stdout, _, _ = run_command("mvn dependency:tree")
    exclusions = []
    
    for item in config:
        dep = item.get("dependency", "")
        patterns = item.get("patterns", [])
        
        dep_parts = dep.split(':')
        if len(dep_parts) < 2:
            continue
        search_str = f"{dep_parts[0]}:{dep_parts[1]}"
        if search_str in stdout:
            found_usage = False
            for pattern in patterns:
                includes = "--include=\\*.java --include=\\*.kt --include=\\*.groovy --include=\\*.yml --include=\\*.yaml --include=\\*.properties --include=\\*.xml"
                cmd = f"grep -ri {includes} '{pattern}' src/ 2>/dev/null"
                out, err, rc = run_command(cmd, allowed_exit_codes=[0, 1])
                if rc == 0 and out.strip():
                    found_usage = True
                    break
            
            if not found_usage:
                exclusions.append({
                    "exclude_group": dep_parts[0],
                    "exclude_artifact": dep_parts[1],
                    "reason": f"No patterns {patterns} found in codebase"
                })
                
    return exclusions

def check_versions():
    print("Checking versions against Spring Boot BOM...", file=sys.stderr)
    pom_path = "pom.xml"
    if not os.path.exists(pom_path):
        return [], []
        
    ET.register_namespace('', 'http://maven.apache.org/POM/4.0.0')
    tree = ET.parse(pom_path)
    root = tree.getroot()
    ns = {'mvn': 'http://maven.apache.org/POM/4.0.0'}
    
    def find_all(node, tag):
        res = node.findall(f"mvn:{tag}", ns)
        if not res:
            res = node.findall(f"{{http://maven.apache.org/POM/4.0.0}}{tag}")
        if not res:
            res = node.findall(tag)
        return res

    def find_one(node, tag):
        res = node.find(f"mvn:{tag}", ns)
        if res is None:
            res = node.find(f"{{http://maven.apache.org/POM/4.0.0}}{tag}")
        if res is None:
            res = node.find(tag)
        return res

    properties = {}
    props_node = find_one(root, 'properties')
    if props_node is not None:
        for p in props_node:
            tag = p.tag
            if '}' in tag:
                tag = tag.split('}')[1]
            properties[tag] = p.text

    explicit_versions = []
    
    def scan_deps(parent_node):
        deps_node = find_one(parent_node, 'dependencies')
        if deps_node is not None:
            for dep in find_all(deps_node, 'dependency'):
                g = find_one(dep, 'groupId')
                a = find_one(dep, 'artifactId')
                v = find_one(dep, 'version')
                if g is not None and a is not None and v is not None:
                    explicit_versions.append({
                        "groupId": g.text,
                        "artifactId": a.text,
                        "version_tag": v.text
                    })
    
    scan_deps(root)
    dep_mgt = find_one(root, 'dependencyManagement')
    if dep_mgt is not None:
        scan_deps(dep_mgt)
        
    parent = find_one(root, 'parent')
    if parent is None:
        return [], []
        
    p_g = find_one(parent, 'groupId')
    p_v = find_one(parent, 'version')
    if p_g is None or p_g.text != 'org.springframework.boot':
        return [], []
        
    sb_version = p_v.text
    
    check_pom = f"""<?xml version="1.0" encoding="UTF-8"?>
<project xmlns="http://maven.apache.org/POM/4.0.0" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance" xsi:schemaLocation="http://maven.apache.org/POM/4.0.0 http://maven.apache.org/xsd/maven-4.0.0.xsd">
    <modelVersion>4.0.0</modelVersion>
    <groupId>check</groupId>
    <artifactId>check</artifactId>
    <version>1.0</version>
    <dependencyManagement>
        <dependencies>
            <dependency>
                <groupId>org.springframework.boot</groupId>
                <artifactId>spring-boot-dependencies</artifactId>
                <version>{sb_version}</version>
                <type>pom</type>
                <scope>import</scope>
            </dependency>
        </dependencies>
    </dependencyManagement>
</project>"""
    
    with open("pom-check.xml", "w") as f:
        f.write(check_pom)
        
    stdout, _, _ = run_command("mvn help:effective-pom -f pom-check.xml")
    if os.path.exists("pom-check.xml"):
        os.remove("pom-check.xml")
    
    try:
        xml_start = stdout.find('<project')
        xml_end = stdout.find('</project>') + len('</project>')
        if xml_start != -1 and xml_end != -1:
            eff_tree = ET.fromstring(stdout[xml_start:xml_end])
            bom_versions = {}
            eff_dm = find_one(eff_tree, 'dependencyManagement')
            if eff_dm is not None:
                eff_deps = find_one(eff_dm, 'dependencies')
                if eff_deps is not None:
                    for dep in find_all(eff_deps, 'dependency'):
                        g = find_one(dep, 'groupId').text
                        a = find_one(dep, 'artifactId').text
                        v_node = find_one(dep, 'version')
                        if v_node is not None:
                            bom_versions[f"{g}:{a}"] = v_node.text
        else:
            return [], []
    except Exception as e:
        print(f"Error parsing effective pom: {e}", file=sys.stderr)
        return [], []

    redundant = []
    overridden = []
    
    for ev in explicit_versions:
        key = f"{ev['groupId']}:{ev['artifactId']}"
        if key in bom_versions:
            bom_v = bom_versions[key]
            actual_v = ev['version_tag']
            if actual_v.startswith('${') and actual_v.endswith('}'):
                prop_name = actual_v[2:-1]
                actual_v = properties.get(prop_name, actual_v)
                
            if actual_v == bom_v:
                redundant.append(ev)
            else:
                ev['bom_version'] = bom_v
                overridden.append(ev)
                
    return redundant, overridden

def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    config_path = os.path.join(script_dir, "heavy-dependencies.json")
    
    unused = analyze_unused_dependencies()
    exclusions = check_heavy_dependencies(config_path)
    redundant, overridden = check_versions()
    
    report = {
        "manual_review_required": unused,
        "hitlist_exclusions": exclusions,
        "redundant_versions": redundant,
        "overridden_versions": overridden
    }
    
    print(json.dumps(report, indent=2))

if __name__ == "__main__":
    main()
