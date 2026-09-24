import Foundation
import HealthKit

public final class HealthAuthorizationCoordinator {
    private let store: HKHealthStore

    public init(store: HKHealthStore = HKHealthStore()) { self.store = store }

    public func requestReadAuthorization() async throws {
        guard HKHealthStore.isHealthDataAvailable() else { throw AuthorizationError.healthDataUnavailable }
        var readTypes = Set<HKObjectType>()
        if let sleep = HKObjectType.categoryType(forIdentifier: .sleepAnalysis) { readTypes.insert(sleep) }
        let quantities: [HKQuantityTypeIdentifier] = [
            .stepCount, .activeEnergyBurned, .appleExerciseTime, .heartRate, .restingHeartRate,
            .heartRateVariabilitySDNN, .respiratoryRate, .oxygenSaturation, .vo2Max, .bodyMass,
            .bodyFatPercentage, .leanBodyMass
        ]
        for identifier in quantities {
            if let type = HKObjectType.quantityType(forIdentifier: identifier) { readTypes.insert(type) }
        }
        readTypes.insert(HKObjectType.workoutType())
        try await store.requestAuthorization(toShare: [], read: readTypes)
    }

    public enum AuthorizationError: Error { case healthDataUnavailable }
}
