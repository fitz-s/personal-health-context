import Foundation

/// Apple sample normalization v1 (contracts/normalization.md), phone side: the requested types and the exact
/// strings export.xml writes for units, category values and workout activity types, so a live sample and its
/// backfill copy produce the same equivalence key. HealthKit-free so it is testable on any platform.
public enum Normalization {
    public static let workoutType = "HKWorkoutTypeIdentifier"
    public static let sleepType = "HKCategoryTypeIdentifierSleepAnalysis"

    /// Requested quantity types, each with the unit it is sent in (the `unit` attribute export.xml writes).
    public static let quantityUnits: [(type: String, unit: String)] = [
        ("HKQuantityTypeIdentifierStepCount", "count"),
        ("HKQuantityTypeIdentifierActiveEnergyBurned", "kcal"),
        ("HKQuantityTypeIdentifierAppleExerciseTime", "min"),
        ("HKQuantityTypeIdentifierHeartRate", "count/min"),
        ("HKQuantityTypeIdentifierRestingHeartRate", "count/min"),
        ("HKQuantityTypeIdentifierHeartRateVariabilitySDNN", "ms"),
        ("HKQuantityTypeIdentifierRespiratoryRate", "count/min"),
        ("HKQuantityTypeIdentifierOxygenSaturation", "%"),
        ("HKQuantityTypeIdentifierVO2Max", "mL/min·kg"),
        ("HKQuantityTypeIdentifierBodyMass", "kg"),
        ("HKQuantityTypeIdentifierBodyFatPercentage", "%"),
        ("HKQuantityTypeIdentifierLeanBodyMass", "kg")
    ]

    /// Every requested category type, with the export.xml string for each raw value.
    public static let categoryValues: [String: [Int: String]] = [
        sleepType: [
            0: "HKCategoryValueSleepAnalysisInBed",
            1: "HKCategoryValueSleepAnalysisAsleepUnspecified",
            2: "HKCategoryValueSleepAnalysisAwake",
            3: "HKCategoryValueSleepAnalysisAsleepCore",
            4: "HKCategoryValueSleepAnalysisAsleepDeep",
            5: "HKCategoryValueSleepAnalysisAsleepREM"
        ]
    ]

    /// `HKWorkoutActivityType` raw value → export.xml `workoutActivityType` suffix (after "HKWorkoutActivityType").
    static let workoutActivities: [UInt: String] = [
        1: "AmericanFootball", 2: "Archery", 3: "AustralianFootball", 4: "Badminton", 5: "Baseball", 6: "Basketball",
        7: "Bowling", 8: "Boxing", 9: "Climbing", 10: "Cricket", 11: "CrossTraining", 12: "Curling", 13: "Cycling",
        14: "Dance", 15: "DanceInspiredTraining", 16: "Elliptical", 17: "EquestrianSports", 18: "Fencing",
        19: "Fishing", 20: "FunctionalStrengthTraining", 21: "Golf", 22: "Gymnastics", 23: "Handball", 24: "Hiking",
        25: "Hockey", 26: "Hunting", 27: "Lacrosse", 28: "MartialArts", 29: "MindAndBody",
        30: "MixedMetabolicCardioTraining", 31: "PaddleSports", 32: "Play", 33: "PreparationAndRecovery",
        34: "Racquetball", 35: "Rowing", 36: "Rugby", 37: "Running", 38: "Sailing", 39: "SkatingSports",
        40: "SnowSports", 41: "Soccer", 42: "Softball", 43: "Squash", 44: "StairClimbing", 45: "SurfingSports",
        46: "Swimming", 47: "TableTennis", 48: "Tennis", 49: "TrackAndField", 50: "TraditionalStrengthTraining",
        51: "Volleyball", 52: "Walking", 53: "WaterFitness", 54: "WaterPolo", 55: "WaterSports", 56: "Wrestling",
        57: "Yoga", 58: "Barre", 59: "CoreTraining", 60: "CrossCountrySkiing", 61: "DownhillSkiing",
        62: "Flexibility", 63: "HighIntensityIntervalTraining", 64: "JumpRope", 65: "Kickboxing", 66: "Pilates",
        67: "Snowboarding", 68: "Stairs", 69: "StepTraining", 70: "WheelchairWalkPace", 71: "WheelchairRunPace",
        72: "TaiChi", 73: "MixedCardio", 74: "HandCycling", 75: "DiscSports", 76: "FitnessGaming", 77: "CardioDance",
        78: "SocialDance", 79: "Pickleball", 80: "Cooldown", 82: "SwimBikeRun", 83: "Transition",
        84: "UnderwaterDiving", 3000: "Other"
    ]

    public static func unit(forQuantity type: String) -> String? { quantityUnits.first { $0.type == type }?.unit }

    public static func workoutActivity(_ rawValue: UInt) -> String {
        "HKWorkoutActivityType" + (workoutActivities[rawValue] ?? "Other")
    }

    /// Category wire fields: the raw int in `value_num`, the export.xml string in `value_text`, unit "".
    /// An unmapped value keeps `value_text` nil rather than inventing a string the importer never produces.
    public static func category(type: String, value: Int) -> (num: Double, text: String?, unit: String) {
        (Double(value), categoryValues[type]?[value], "")
    }

    /// Workout wire fields: the activity type string in `value_text`, duration seconds in `value_num`, unit "s".
    public static func workout(activity rawValue: UInt, duration: TimeInterval) -> (num: Double, text: String, unit: String) {
        (duration, workoutActivity(rawValue), "s")
    }
}
