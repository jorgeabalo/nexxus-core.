# AITA Car — Android Auto native prototype

Source only; not compiled, installed, approved or tested on a head unit. No Android SDK/Gradle/JDK runtime was available in this workspace. No APK is included. This app does not connect to the local web session or backend. It contains two public Chicago landmark examples, not nearby gas/charging results.

Open this folder in Android Studio. Use JDK17, Gradle8.11.1, Android SDK35 and the declared Car App Library1.7.0 dependencies. Compile the debug app, install on a test Android phone and validate with the Android Auto Desktop Head Unit before any road/hardware trial. No Gradle wrapper binary is bundled; Android Studio/Gradle tooling must initialize it. Release host validation intentionally does not allow all hosts.

POI template is rendered by Android Auto. Tapping a landmark delegates navigation with CarContext.ACTION_NAVIGATE to a compatible installed navigation app. This source does not project the web cockpit, implement turn-by-turn routing, microphone/AI, subscriptions or BLE. The older PlaceListMapTemplate is used for host-rendered maps; migrate to app-rendered MapWithContentTemplate when a native mapping provider is selected.

Next required work: compile and instrumented tests; authenticated per-tenant pairing with production HTTPS backend (never reuse demo cookies); consent-based GPS and live POI provider; connector filters for EV; ES/EN resources; host/distraction review, distribution and hardware tests. Official references:
- https://developer.android.com/training/cars/apps/poi
- https://developer.android.com/training/cars/apps/library
- https://developer.android.com/reference/androidx/car/app/CarContext
- https://developer.android.com/training/cars/testing/dhu
