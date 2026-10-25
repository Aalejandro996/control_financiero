plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
    id("org.jetbrains.kotlin.plugin.serialization")
}
android {
    namespace = "app.quincena"
    compileSdk = 35
    defaultConfig {
        applicationId = "app.quincena"
        minSdk = 26
        targetSdk = 35
        // En GitHub Actions la versión sube sola con cada compilación (VERSION_CODE / VERSION_NAME).
        // En tu Mac se usan estos valores; mantenlos por debajo de 100 para que las versiones automáticas siempre ganen.
        versionCode = System.getenv("VERSION_CODE")?.toIntOrNull() ?: 8
        versionName = System.getenv("VERSION_NAME") ?: "1.6.0"
    }
    // Firma automática solo si existen las variables (las pone GitHub Actions). En tu Mac sigues usando el asistente.
    val ciKeystore = System.getenv("KEYSTORE_FILE")
    signingConfigs {
        if (ciKeystore != null) create("ci") {
            storeFile = file(ciKeystore)
            storePassword = System.getenv("KEYSTORE_PASSWORD")
            keyAlias = System.getenv("KEY_ALIAS")
            keyPassword = System.getenv("KEY_PASSWORD")
        }
    }
    buildFeatures { compose = true; buildConfig = true }
    compileOptions { sourceCompatibility = JavaVersion.VERSION_17; targetCompatibility = JavaVersion.VERSION_17 }
    kotlinOptions { jvmTarget = "17" }
    buildTypes {
        // Pruebas en el emulador: 10.0.2.2 es tu Mac; el servidor local corre en el puerto 5001.
        debug { buildConfigField("String", "API_BASE", "\"http://10.0.2.2:5001\"") }
        release {
            isMinifyEnabled = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            buildConfigField("String", "API_BASE", "\"https://control-financiero-wrm5.onrender.com\"")
            if (ciKeystore != null) signingConfig = signingConfigs.getByName("ci")
        }
    }
}
dependencies {
    implementation(platform("androidx.compose:compose-bom:2024.09.00"))
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.ui:ui")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.fragment:fragment-ktx:1.8.3")
    implementation("androidx.biometric:biometric:1.1.0")
    implementation("androidx.work:work-runtime-ktx:2.9.1")
    implementation("org.jetbrains.kotlinx:kotlinx-serialization-json:1.7.2")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
}
