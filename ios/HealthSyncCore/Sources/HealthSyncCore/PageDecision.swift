import Foundation

/// What one anchored-query page means for its stream. Initial-history coverage and page exhaustion are different
/// facts: coverage becomes complete once and stays so, while every full page means more changes are waiting.
public struct PageDecision: Equatable, Sendable {
    public static let limit = 500
    /// Coverage flag to record: the bounded initial-history window has been read to its end.
    public let historyComplete: Bool
    /// The page was full, so query again from its anchor without waiting for another observer event.
    public let queryAgain: Bool

    /// `samples` and `deleted` are the raw counts HealthKit returned; its limit bounds them together.
    public init(historyWasComplete: Bool, samples: Int, deleted: Int) {
        queryAgain = samples + deleted >= Self.limit
        historyComplete = historyWasComplete || !queryAgain
    }
}
