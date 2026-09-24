import Foundation
import HealthKit
import HealthSyncCore

public final class HealthAuthorizationCoordinator {
    private let store: HKHealthStore

    public init(store: HKHealthStore = HKHealthStore()) { self.store = store }

    public func requestReadAuthorization() async throws {
        guard HKHealthStore.isHealthDataAvailable() else { throw AuthorizationError.healthDataUnavailable }
        try await store.requestAuthorization(toShare: [], read: Set(HealthTypes.requested))
    }

    public enum AuthorizationError: Error { case healthDataUnavailable }
}

/// The HealthKit types this helper reads and syncs; derived from the normalization tables so every requested type
/// has a contract mapping.
enum HealthTypes {
    static var requested: [HKSampleType] {
        let categories = Normalization.categoryValues.keys.sorted()
            .compactMap { HKObjectType.categoryType(forIdentifier: HKCategoryTypeIdentifier(rawValue: $0)) }
        let quantities = Normalization.quantityUnits
            .compactMap { HKObjectType.quantityType(forIdentifier: HKQuantityTypeIdentifier(rawValue: $0.type)) }
        return categories + quantities + [HKObjectType.workoutType()]
    }
}
