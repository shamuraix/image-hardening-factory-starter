import groovy.json.JsonSlurperClassic


def parameterEnabled(String name, boolean defaultValue = false) {
    def value = params[name]
    return value == null ? defaultValue : value as boolean
}


def parameterText(String name, String defaultValue = '') {
    def value = params[name]
    return value == null ? defaultValue : value.toString().trim()
}


def safeName(String value) {
    return value.replaceAll(/[^A-Za-z0-9_.-]/, '-')
}


def requiredSetting(String name) {
    def value = env[name]?.trim()
    if (!value) {
        error("Jenkins setting ${name} is required")
    }
    return value
}


def stageEnabled(String name) {
    return parameterEnabled("FACTORY_ENABLE_${name}", name == 'VALIDATE')
}


def artifactName(String image, String stageName) {
    return safeName("${env.BUILD_TAG}-${image}-${stageName}")
}


def credentialBindings(List<Map> specifications) {
    return specifications.collect { specification ->
        def credentialsId = requiredSetting(specification.idVariable as String)
        switch (specification.type) {
            case 'file':
                return file(
                    credentialsId: credentialsId,
                    variable: specification.variable as String,
                )
            case 'usernamePassword':
                return usernamePassword(
                    credentialsId: credentialsId,
                    usernameVariable: specification.usernameVariable as String,
                    passwordVariable: specification.passwordVariable as String,
                )
            default:
                return string(
                    credentialsId: credentialsId,
                    variable: specification.variable as String,
                )
        }
    }
}


def withStageCredentials(List<Map> specifications, Closure body) {
    if (specifications.isEmpty()) {
        body()
        return
    }
    withCredentials(credentialBindings(specifications)) {
        body()
    }
}


def inFactoryPod(
    String templateVariable,
    String imageVariable,
    String imageName,
    List<String> additionalEnvironment = [],
    Closure body
) {
    def podTemplateName = requiredSetting(templateVariable)
    def runnerImage = requiredSetting(imageVariable)
    podTemplate(
        inheritFrom: podTemplateName,
        containers: [
            containerTemplate(
                name: 'factory',
                image: runnerImage,
                alwaysPullImage: true,
                command: 'sleep',
                args: '99d',
                ttyEnabled: true,
            ),
        ],
    ) {
        node(POD_LABEL) {
            container('factory') {
                deleteDir()
                def scmState = checkout(scm)
                def catalogDirectory = env.FACTORY_CATALOG_DIR?.trim() ?: 'catalog/images'
                def buildIdentity = safeName(env.BUILD_TAG ?: "${env.JOB_NAME}-${env.BUILD_NUMBER}")
                withEnv([
                    "PYTHONPATH=${pwd()}",
                    "FACTORY_IMAGE=${imageName}",
                    "FACTORY_CATALOG_DIR=${catalogDirectory}",
                    "FACTORY_CATALOG_FILE=${catalogDirectory}/${imageName}.yaml",
                    "FACTORY_WORK_DIR=work/${imageName}",
                    "FACTORY_WORKSPACE=${pwd()}",
                    "FACTORY_BUILD_ID=${buildIdentity}",
                    "FACTORY_BUILD_URL=${env.BUILD_URL ?: ''}",
                    "FACTORY_COMMIT_SHA=${scmState.GIT_COMMIT ?: env.GIT_COMMIT ?: ''}",
                    "FACTORY_SOURCE_URL=${scmState.GIT_URL ?: env.GIT_URL ?: ''}",
                    "FACTORY_BRANCH_NAME=${env.BRANCH_NAME ?: ''}",
                    "FACTORY_CHANGE_ID=${env.CHANGE_ID ?: ''}",
                    "FACTORY_RUNNER_ID=${env.NODE_NAME}",
                    "FACTORY_JOB_ID=${safeName("${buildIdentity}-${imageName}")}",
                    'HOME=/home/factory',
                    'XDG_RUNTIME_DIR=/tmp/factory-runtime',
                    'CONTAINERS_STORAGE_CONF=/home/factory/.config/containers/storage.conf',
                    'STORAGE_DRIVER=vfs',
                    'BUILDAH_ISOLATION=rootless',
                ] + additionalEnvironment) {
                    sh 'mkdir -p "${FACTORY_WORK_DIR}" "${XDG_RUNTIME_DIR}" && chmod 0700 "${XDG_RUNTIME_DIR}"'
                    body()
                }
            }
        }
    }
}


def runFactoryStage(
    String image,
    String stageName,
    String templateVariable,
    String runnerImageVariable,
    List<String> inputArtifacts,
    String outputPatterns,
    String command,
    List<Map> credentials = [],
    List<String> additionalEnvironment = [],
    boolean nonBlocking = false,
    String lockName = ''
) {
    def outputArtifact = artifactName(image, stageName)
    stage("${stageName}: ${image}") {
        inFactoryPod(
            templateVariable,
            runnerImageVariable,
            image,
            additionalEnvironment,
        ) {
            inputArtifacts.findAll { it }.each { unstash(it) }
            def execute = {
                withStageCredentials(credentials) {
                    sh(command)
                }
            }
            def executeWithLock = {
                if (lockName) {
                    lock(resource: lockName) {
                        execute()
                    }
                } else {
                    execute()
                }
            }
            if (nonBlocking) {
                catchError(buildResult: 'SUCCESS', stageResult: 'UNSTABLE') {
                    executeWithLock()
                }
            } else {
                executeWithLock()
            }
            stash(
                name: outputArtifact,
                includes: outputPatterns,
                allowEmpty: nonBlocking,
                useDefaultExcludes: false,
            )
            archiveArtifacts(
                artifacts: outputPatterns,
                allowEmptyArchive: nonBlocking,
                fingerprint: stageName in ['build', 'import', 'attest'],
            )
        }
    }
    return outputArtifact
}


def isProtectedBranch() {
    def defaultBranch = requiredSetting('FACTORY_DEFAULT_BRANCH')
    return !env.CHANGE_ID && env.BRANCH_NAME == defaultBranch
}


def validateStageDependencies() {
    def dependencies = [
        PREPARE: ['VALIDATE'],
        BUILD: ['PREPARE'],
        SBOM: ['BUILD'],
        SCAN: ['BUILD', 'SBOM'],
        FCS: ['BUILD'],
        COMPLIANCE: ['BUILD', 'SBOM'],
        TEST: ['BUILD'],
        GATE: ['BUILD', 'SBOM', 'FCS', 'COMPLIANCE', 'TEST'],
        REMEDIATE: ['GATE', 'SBOM'],
        REMEDIATION_BRANCH: ['REMEDIATE'],
        IMPORT: ['PREPARE', 'BUILD', 'GATE'],
        ATTEST: ['IMPORT', 'GATE', 'SBOM', 'FCS', 'COMPLIANCE', 'TEST'],
        PROMOTE: ['IMPORT', 'ATTEST'],
    ]
    dependencies.each { stageName, requirements ->
        if (stageEnabled(stageName as String)) {
            requirements.each { requirement ->
                if (!stageEnabled(requirement as String)) {
                    error("FACTORY_ENABLE_${stageName} requires FACTORY_ENABLE_${requirement}")
                }
            }
        }
    }
}


def runImage(Map imageDefinition, Set<String> selectedImages, String releaseApprover) {
    def image = imageDefinition.name as String
    def catalogFile = imageDefinition.catalogFile as String
    def baseImage = imageDefinition.baseImage as String
    def catalogEnvironment = ["FACTORY_CATALOG_FILE=${catalogFile}"]
    def validateArtifact = ''
    def prepareArtifact = ''
    def buildArtifact = ''
    def sbomArtifact = ''
    def scanArtifact = ''
    def fcsArtifact = ''
    def complianceArtifact = ''
    def testArtifact = ''
    def gateArtifact = ''
    def remediationArtifact = ''
    def importArtifact = ''
    def attestArtifact = ''

    if (stageEnabled('VALIDATE')) {
        validateArtifact = runFactoryStage(
            image,
            'validate',
            'FACTORY_K8S_OFFLINE_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [],
            "work/${image}/validation.json",
            'python3 -m factory.cli validate --catalog "${FACTORY_CATALOG_DIR}" && ' +
                'scripts/validate_image.sh "${FACTORY_CATALOG_FILE}"',
            [],
            catalogEnvironment,
        )
    }

    if (stageEnabled('PREPARE')) {
        def prepareInputs = [validateArtifact]
        if (baseImage && selectedImages.contains(baseImage) && stageEnabled('BUILD')) {
            prepareInputs.add(artifactName(baseImage, 'build'))
        }
        prepareArtifact = runFactoryStage(
            image,
            'prepare',
            'FACTORY_K8S_OFFLINE_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            prepareInputs,
            "work/${image}/context/**,work/${image}/resource-lock.json," +
                "work/${image}/resource-lock.sig,work/${image}/build.env," +
                "work/${image}/base.oci.tar",
            'scripts/prepare_context.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [
                [
                    type: 'string',
                    idVariable: 'ARTIFACTORY_READ_CREDENTIAL_ID',
                    variable: 'ARTIFACTORY_READ_TOKEN',
                ],
                [
                    type: 'file',
                    idVariable: 'COSIGN_INTAKE_PUBLIC_KEY_CREDENTIAL_ID',
                    variable: 'COSIGN_INTAKE_PUBLIC_KEY',
                ],
            ],
            catalogEnvironment,
        )
    }

    if (stageEnabled('BUILD')) {
        buildArtifact = runFactoryStage(
            image,
            'build',
            'FACTORY_K8S_BUILDAH_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [prepareArtifact],
            "work/${image}/image.oci.tar,work/${image}/image-metadata.json," +
                "work/${image}/build.env",
            'scripts/build_image.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [[
                type: 'string',
                idVariable: 'ARTIFACTORY_READ_CREDENTIAL_ID',
                variable: 'ARTIFACTORY_READ_TOKEN',
            ]],
            catalogEnvironment,
        )
    }

    if (stageEnabled('SBOM')) {
        sbomArtifact = runFactoryStage(
            image,
            'sbom',
            'FACTORY_K8S_OFFLINE_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [buildArtifact],
            "work/${image}/evidence/sbom.*",
            'scripts/generate_sbom.sh "${FACTORY_WORK_DIR}/image.oci.tar" ' +
                '"${FACTORY_WORK_DIR}/evidence"',
            [],
            catalogEnvironment,
        )
    }

    if (stageEnabled('SCAN')) {
        scanArtifact = runFactoryStage(
            image,
            'scan',
            'FACTORY_K8S_OFFLINE_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [buildArtifact, sbomArtifact],
            "work/${image}/evidence/scans/**,work/${image}/evidence/findings.json," +
                "work/${image}/evidence/database-status.json",
            'scripts/scan_image.sh "${FACTORY_WORK_DIR}"',
            [],
            catalogEnvironment,
            true,
        )
    }

    if (stageEnabled('FCS')) {
        fcsArtifact = runFactoryStage(
            image,
            'fcs',
            'FACTORY_K8S_FCS_POD_TEMPLATE',
            'FACTORY_FCS_RUNNER_IMAGE',
            [buildArtifact],
            "work/${image}/evidence/scans/fcs/**",
            'scripts/fcs_scan_image.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [
                [
                    type: 'string',
                    idVariable: 'FALCON_CLIENT_ID_CREDENTIAL_ID',
                    variable: 'FALCON_CLIENT_ID',
                ],
                [
                    type: 'string',
                    idVariable: 'FALCON_CLIENT_SECRET_CREDENTIAL_ID',
                    variable: 'FALCON_CLIENT_SECRET',
                ],
            ],
            catalogEnvironment,
        )
    }

    if (stageEnabled('COMPLIANCE')) {
        complianceArtifact = runFactoryStage(
            image,
            'compliance',
            'FACTORY_K8S_FIPS_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [buildArtifact, sbomArtifact],
            "work/${image}/evidence/compliance/**",
            'scripts/compliance_scan.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [],
            catalogEnvironment,
        )
    }

    if (stageEnabled('TEST')) {
        testArtifact = runFactoryStage(
            image,
            'test',
            'FACTORY_K8S_TEST_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [buildArtifact],
            "work/${image}/evidence/tests/**",
            'scripts/run_tests.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [],
            catalogEnvironment,
        )
    }

    if (stageEnabled('GATE')) {
        gateArtifact = runFactoryStage(
            image,
            'gate',
            'FACTORY_K8S_OFFLINE_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [
                buildArtifact,
                sbomArtifact,
                scanArtifact,
                fcsArtifact,
                complianceArtifact,
                testArtifact,
            ],
            "work/${image}/evidence/gate-input.json," +
                "work/${image}/evidence/gate-result.json," +
                "work/${image}/evidence/findings.json," +
                "work/${image}/evidence/database-status.json," +
                "work/${image}/gate.env",
            'scripts/evaluate_gate.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [],
            catalogEnvironment,
        )
    }

    if (stageEnabled('REMEDIATE')) {
        remediationArtifact = runFactoryStage(
            image,
            'remediate',
            'FACTORY_K8S_AI_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [gateArtifact, sbomArtifact, scanArtifact],
            "work/${image}/evidence/remediation/**",
            'scripts/ai_remediation.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [[
                type: 'string',
                idVariable: 'AI_API_KEY_CREDENTIAL_ID',
                variable: 'AI_API_KEY',
            ]],
            catalogEnvironment,
            true,
        )
    }

    if (stageEnabled('REMEDIATION_BRANCH')) {
        runFactoryStage(
            image,
            'remediation-branch',
            'FACTORY_K8S_REMEDIATION_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [remediationArtifact],
            "work/${image}/evidence/remediation/branch.env",
            'scripts/publish_remediation_branch.sh ' +
                '"${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [[
                type: 'usernamePassword',
                idVariable: 'SCM_REMEDIATION_CREDENTIAL_ID',
                usernameVariable: 'SCM_REMEDIATION_USERNAME',
                passwordVariable: 'SCM_REMEDIATION_TOKEN',
            ]],
            catalogEnvironment,
            true,
        )
    }

    if (stageEnabled('IMPORT')) {
        def protectedPublish = isProtectedBranch()
        def importCredentials = protectedPublish ? [[
            type: 'string',
            idVariable: 'ARTIFACTORY_WRITE_CREDENTIAL_ID',
            variable: 'ARTIFACTORY_WRITE_TOKEN',
        ]] : []
        def importLock = protectedPublish ?
            "${requiredSetting('FACTORY_IMPORT_LOCK_PREFIX')}-${safeName(image)}" : ''
        importArtifact = runFactoryStage(
            image,
            'import',
            'FACTORY_K8S_IMPORT_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [prepareArtifact, buildArtifact, gateArtifact],
            "work/${image}/import-result.json,work/${image}/import.env",
            'scripts/import_image.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            importCredentials,
            catalogEnvironment + ["FACTORY_PROTECTED_PUBLISH=${protectedPublish}"],
            false,
            importLock,
        )
    }

    if (stageEnabled('ATTEST')) {
        if (!isProtectedBranch()) {
            error('Signing is allowed only for the configured default branch')
        }
        attestArtifact = runFactoryStage(
            image,
            'attest',
            'FACTORY_K8S_SIGNING_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [
                prepareArtifact,
                buildArtifact,
                sbomArtifact,
                fcsArtifact,
                complianceArtifact,
                testArtifact,
                gateArtifact,
                importArtifact,
            ],
            "work/${image}/evidence/signing-result.json," +
                "work/${image}/evidence/provenance.json",
            'scripts/sign_and_attest.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [
                [
                    type: 'file',
                    idVariable: 'COSIGN_KEY_CREDENTIAL_ID',
                    variable: 'COSIGN_KEY_PATH',
                ],
                [
                    type: 'string',
                    idVariable: 'COSIGN_PASSWORD_CREDENTIAL_ID',
                    variable: 'COSIGN_PASSWORD',
                ],
                [
                    type: 'string',
                    idVariable: 'ARTIFACTORY_SIGN_CREDENTIAL_ID',
                    variable: 'ARTIFACTORY_SIGN_TOKEN',
                ],
            ],
            catalogEnvironment + ["FACTORY_APPROVER_ID=${releaseApprover}"],
        )
    }

    if (stageEnabled('PROMOTE')) {
        if (!isProtectedBranch()) {
            error('Promotion is allowed only for the configured default branch')
        }
        runFactoryStage(
            image,
            'promote',
            'FACTORY_K8S_PROMOTION_POD_TEMPLATE',
            'FACTORY_RUNNER_IMAGE',
            [importArtifact, attestArtifact],
            "work/${image}/promotion-result.json",
            'scripts/promote_image.sh "${FACTORY_CATALOG_FILE}" "${FACTORY_WORK_DIR}"',
            [
                [
                    type: 'file',
                    idVariable: 'COSIGN_PUBLIC_KEY_CREDENTIAL_ID',
                    variable: 'COSIGN_PUBLIC_KEY',
                ],
                [
                    type: 'string',
                    idVariable: 'ARTIFACTORY_RELEASE_CREDENTIAL_ID',
                    variable: 'ARTIFACTORY_RELEASE_TOKEN',
                ],
            ],
            catalogEnvironment + ["FACTORY_APPROVER_ID=${releaseApprover}"],
            false,
            "${requiredSetting('FACTORY_PROMOTION_LOCK_PREFIX')}-" +
                "${safeName(parameterText('FACTORY_RELEASE_ENV', 'commercial'))}-" +
                safeName(image),
        )
    }
}


properties([
    buildDiscarder(logRotator(numToKeepStr: '20')),
    disableConcurrentBuilds(abortPrevious: true),
    parameters([
        string(
            name: 'FACTORY_CHANGED_IMAGES',
            defaultValue: '',
            description: 'Comma-separated catalog roots; empty selects all images',
        ),
        choice(
            name: 'FACTORY_RELEASE_ENV',
            choices: ['commercial', 'gov1', 'gov2'],
            description: 'Release policy environment',
        ),
        booleanParam(name: 'FACTORY_ENABLE_VALIDATE', defaultValue: true),
        booleanParam(name: 'FACTORY_ENABLE_PREPARE', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_BUILD', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_SBOM', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_SCAN', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_FCS', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_COMPLIANCE', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_TEST', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_GATE', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_REMEDIATE', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_REMEDIATION_BRANCH', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_IMPORT', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_ATTEST', defaultValue: false),
        booleanParam(name: 'FACTORY_ENABLE_PROMOTE', defaultValue: false),
    ]),
])

validateStageDependencies()
def releaseEnvironment = parameterText('FACTORY_RELEASE_ENV', 'commercial')
def releaseApprover = env.BUILD_USER_ID ?: 'jenkins'
if ((stageEnabled('ATTEST') || stageEnabled('PROMOTE')) && releaseEnvironment.startsWith('gov')) {
    stage('Release approval') {
        releaseApprover = input(
            message: "Authorize ${releaseEnvironment} signing and promotion",
            ok: 'Authorize',
            submitter: requiredSetting('FACTORY_GOV_APPROVERS'),
            submitterParameter: 'FACTORY_APPROVER_ID',
        ).toString()
    }
}

def plan
stage('plan') {
    inFactoryPod(
        'FACTORY_K8S_OFFLINE_POD_TEMPLATE',
        'FACTORY_RUNNER_IMAGE',
        'plan',
        ["FACTORY_RELEASE_ENV=${releaseEnvironment}"],
    ) {
        withEnv(["FACTORY_CHANGED_IMAGES=${parameterText('FACTORY_CHANGED_IMAGES')}"]) {
            sh 'scripts/render_jenkins_plan.sh generated-jenkins-plan.json'
        }
        plan = new JsonSlurperClassic().parseText(readFile('generated-jenkins-plan.json'))
        archiveArtifacts artifacts: 'generated-jenkins-plan.json', fingerprint: true
    }
}

def imagesByName = plan.images.collectEntries { [(it.name as String): it] }
def selectedImages = imagesByName.keySet() as Set<String>
plan.waves.eachWithIndex { wave, index ->
    stage("image wave ${index + 1}") {
        def branches = [:]
        wave.each { imageName ->
            def selectedImage = imagesByName[imageName as String]
            branches[imageName as String] = {
                runImage(selectedImage as Map, selectedImages, releaseApprover)
            }
        }
        parallel(branches)
    }
}
